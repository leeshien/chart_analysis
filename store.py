import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,          -- ISO yyyy-mm-dd
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS universe (
    ticker     TEXT PRIMARY KEY,
    name       TEXT,
    added_on   TEXT,
    removed_on TEXT,
    sector     TEXT,
    sub_tier   TEXT,
    market_cap REAL,
    source     TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS levels (
    ticker       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    price        REAL NOT NULL,
    kind         TEXT,
    strength     REAL,
    touches      INTEGER,
    episodes     INTEGER,
    score        REAL,
    is_major     INTEGER,
    is_primary   INTEGER,
    nearest_major_support INTEGER,
    reasoning    TEXT,
    generated_at TEXT,
    PRIMARY KEY (ticker, timeframe, price)
);

CREATE INDEX IF NOT EXISTS idx_levels_tf ON levels(timeframe, ticker);

CREATE TABLE IF NOT EXISTS visits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    price        REAL NOT NULL,
    kind         TEXT,
    opened_on    TEXT,
    last_in_band TEXT,
    closed_on    TEXT
);

CREATE INDEX IF NOT EXISTS idx_visits_open
    ON visits(ticker, timeframe, closed_on);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    timeframe  TEXT,
    price      REAL,
    kind       TEXT,
    event      TEXT NOT NULL,
    confluence INTEGER DEFAULT 0,
    score      REAL,
    close      REAL,
    delivered  INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS watches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    price        REAL NOT NULL,
    title        TEXT,
    note         TEXT,
    created_on   TEXT,
    triggered_on TEXT,
    active       INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(active, ticker);
"""


def connect(db_path):
    """Open the database, creating parent directories as needed."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads while a job writes
    return conn


_ADDED_COLUMNS = {
    "universe": {"sector": "TEXT", "sub_tier": "TEXT",
                 "market_cap": "REAL", "source": "TEXT",
                 "sub_industry": "TEXT"},
    "alerts": {"title": "TEXT", "setup": "TEXT", "daily_trend": "TEXT",
               "weekly_trend": "TEXT", "is_major": "INTEGER",
               "pattern": "TEXT", "setup_ts": "TEXT", "note": "TEXT",
               "outlook": "TEXT"},
}


def _migrate(conn):
    """Add columns that a pre-existing table is missing.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a
    schema change alone would silently fail to reach the live database — which
    holds 4.4M bars and is not worth rebuilding for four columns.
    """
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def init_db(conn):
    """Create every table if absent, then add any missing columns."""
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn


_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def upsert_bars(conn, ticker, rows) -> int:
    """Insert or replace bars. ``rows`` is (date, open, high, low, close, volume).

    Upsert rather than insert so the overlap window re-written on every run
    corrects any values the provider revised.
    """
    rows = list(rows)
    conn.executemany(
        "INSERT INTO bars (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker, date) DO UPDATE SET "
        "open=excluded.open, high=excluded.high, low=excluded.low, "
        "close=excluded.close, volume=excluded.volume",
        [(ticker, *r) for r in rows],
    )
    conn.commit()
    return len(rows)


def last_bar_date(conn, ticker):
    """ISO date of the newest stored bar, or None if the ticker is unknown."""
    row = conn.execute("SELECT MAX(date) AS d FROM bars WHERE ticker = ?",
                       (ticker,)).fetchone()
    return row["d"] if row else None


def read_bars(conn, ticker, start=None, end=None) -> pd.DataFrame:
    """Stored bars as an OHLCV frame indexed by date, oldest first."""
    sql = "SELECT date, open, high, low, close, volume FROM bars WHERE ticker = ?"
    params = [ticker]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    sql += " ORDER BY date"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return pd.DataFrame(columns=_COLUMNS, index=pd.DatetimeIndex([], name="Date"))

    # sqlite3.Row exposes both keys() and integer indexing, which pandas can read
    # either way; plain tuples remove the ambiguity.
    df = pd.DataFrame([tuple(r) for r in rows],
                      columns=["Date", *[c.lower() for c in _COLUMNS]])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    df.columns = _COLUMNS
    return df


def closes_by_date(conn, ticker, since=None) -> dict:
    """{iso_date: close} for the drift check, which only compares closes."""
    sql = "SELECT date, close FROM bars WHERE ticker = ?"
    params = [ticker]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    return {r["date"]: r["close"] for r in conn.execute(sql, params)}


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def add_constituents(conn, pairs, on_date, source="sp500"):
    """Add or reactivate universe members.

    ``pairs`` is (ticker, name), (ticker, name, sector) or (ticker, name,
    sector, sub_industry). Re-adding clears any removal date, so a ticker that
    left the index and returned is active again with its history intact.
    """
    rows = []
    for p in pairs:
        ticker, name = p[0], p[1]
        sector = p[2] if len(p) > 2 else None
        sub_industry = p[3] if len(p) > 3 else None
        rows.append((ticker, name, on_date, sector, sub_industry, source))

    conn.executemany(
        "INSERT INTO universe (ticker, name, added_on, removed_on, sector, "
        " sub_industry, source) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET name = excluded.name, "
        "removed_on = NULL, "
        "sector = COALESCE(excluded.sector, universe.sector), "
        "sub_industry = COALESCE(excluded.sub_industry, universe.sub_industry), "
        "source = COALESCE(universe.source, excluded.source)",
        rows,
    )
    conn.commit()


def set_universe_fields(conn, ticker, **fields):
    """Update sector, sub_tier, market_cap or source for one ticker."""
    allowed = {"sector", "sub_tier", "market_cap", "source", "name"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE universe SET {clause} WHERE ticker = ?",
                 [*sets.values(), ticker])
    conn.commit()


def universe_rows(conn, active_only=True) -> list:
    """Full universe records, for grouping alerts by sector and tier."""
    sql = "SELECT * FROM universe"
    if active_only:
        sql += " WHERE removed_on IS NULL"
    sql += " ORDER BY ticker"
    return [dict(r) for r in conn.execute(sql)]


def remove_constituents(conn, tickers, on_date):
    """Mark tickers as no longer in the index. History is kept, not deleted."""
    conn.executemany("UPDATE universe SET removed_on = ? WHERE ticker = ?",
                     [(on_date, t) for t in tickers])
    conn.commit()


def active_universe(conn, source=None) -> list:
    """Active tickers, optionally only those from one source.

    The source filter matters: the S&P refresh computes removals by diffing
    against Wikipedia, so without scoping it would retire every chain-only
    ticker as though it had left the index.
    """
    sql = "SELECT ticker FROM universe WHERE removed_on IS NULL"
    params = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY ticker"
    return [r["ticker"] for r in conn.execute(sql, params)]


_LEVEL_FIELDS = ["price", "kind", "strength", "touches", "episodes", "score",
                 "is_major", "is_primary", "nearest_major_support", "reasoning"]


def replace_levels(conn, ticker, timeframe, rows, generated_at) -> int:
    """Swap this ticker's levels for one timeframe. Returns the number written.

    An empty rows writes nothing and leaves the previous set in place. A
    refresh that parsed to nothing is a failure, not a discovery that the chart
    has no levels, and wiping them would silently disarm every alert on that
    ticker until the next successful refresh.
    """
    rows = list(rows)
    if not rows:
        return 0

    conn.execute("DELETE FROM levels WHERE ticker = ? AND timeframe = ?",
                 (ticker, timeframe))
    conn.executemany(
        "INSERT OR REPLACE INTO levels "
        "(ticker, timeframe, price, kind, strength, touches, episodes, score, "
        " is_major, is_primary, nearest_major_support, reasoning, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(ticker, timeframe, r["price"], r["kind"], r["strength"], r["touches"],
          r["episodes"], r["score"], int(bool(r["is_major"])),
          int(bool(r["is_primary"])), int(bool(r["nearest_major_support"])),
          r["reasoning"], generated_at) for r in rows],
    )
    conn.commit()
    return len(rows)


def read_levels(conn, ticker, timeframe) -> list:
    """This ticker's levels for one timeframe, strongest first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM levels WHERE ticker = ? AND timeframe = ? "
        "ORDER BY score DESC, price", (ticker, timeframe))]


def newest_levels_date(conn, timeframe):
    """Most recent generated_at for a timeframe, for the staleness guard."""
    row = conn.execute("SELECT MAX(generated_at) AS d FROM levels WHERE timeframe = ?",
                       (timeframe,)).fetchone()
    return row["d"] if row else None


def open_visit(conn, ticker, timeframe, price, kind, on_date):
    """Record the first touch of a level, which suppresses further touches."""
    conn.execute(
        "INSERT INTO visits (ticker, timeframe, price, kind, opened_on, last_in_band) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ticker, timeframe, price, kind, on_date, on_date))
    conn.commit()


def find_open_visit(conn, ticker, timeframe, price, tol_pct):
    """An open visit within ``tol_pct`` of ``price``, or None.

    Matched by proximity rather than equality: levels are re-derived on every
    refresh, so a support at 100.00 can come back as 99.60. Keying on the exact
    price would treat that as a new level and alert twice for one line.
    """
    lo, hi = price * (1 - tol_pct), price * (1 + tol_pct)
    row = conn.execute(
        "SELECT * FROM visits WHERE ticker = ? AND timeframe = ? "
        "AND closed_on IS NULL AND price BETWEEN ? AND ? "
        "ORDER BY ABS(price - ?) LIMIT 1",
        (ticker, timeframe, lo, hi, price)).fetchone()
    return dict(row) if row else None


def extend_visit(conn, ticker, timeframe, price, on_date, tol_pct=0.02):
    """Mark that price is still in the band, without alerting again."""
    v = find_open_visit(conn, ticker, timeframe, price, tol_pct)
    if v:
        conn.execute("UPDATE visits SET last_in_band = ? WHERE id = ?",
                     (on_date, v["id"]))
        conn.commit()


def close_visits_before(conn, today, gap_days):
    """Close visits whose band price left more than ``gap_days`` ago.

    A closed visit frees that level to alert again. Without this a level touched
    once would stay muted for ever.
    """
    conn.execute(
        "UPDATE visits SET closed_on = ? WHERE closed_on IS NULL "
        "AND julianday(?) - julianday(last_in_band) > ?",
        (today, today, gap_days))
    conn.commit()


_ALERT_FIELDS = ["ts", "ticker", "timeframe", "price", "kind", "event", "title",
                 "confluence", "score", "close", "delivered", "setup",
                 "daily_trend", "weekly_trend", "is_major", "pattern",
                 "setup_ts", "note", "outlook"]


def record_alert(conn, row):
    conn.execute(
        f"INSERT INTO alerts ({', '.join(_ALERT_FIELDS)}) "
        f"VALUES ({', '.join('?' * len(_ALERT_FIELDS))})",
        [row.get(f) for f in _ALERT_FIELDS])
    conn.commit()


def alerts_on(conn, ts) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE ts = ? ORDER BY confluence DESC, score DESC",
        (ts,))]


def undelivered_alerts(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE delivered = 0 ORDER BY ts, id")]


def mark_delivered(conn, ids):
    conn.executemany("UPDATE alerts SET delivered = 1 WHERE id = ?",
                     [(i,) for i in ids])
    conn.commit()


def add_watch(conn, ticker, price, title=None, note=None, on_date=None) -> int:
    """Arm a user-specified price alert. Returns its id.

    Watches are separate from the Gemini levels: they are prices the user named,
    and they fire once rather than being re-derived on a refresh.
    """
    cur = conn.execute(
        "INSERT INTO watches (ticker, price, title, note, created_on, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (ticker, float(price), title, note, on_date))
    conn.commit()
    return cur.lastrowid


def active_watches(conn, ticker=None) -> list:
    sql = "SELECT * FROM watches WHERE active = 1"
    params = []
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker)
    sql += " ORDER BY ticker, price"
    return [dict(r) for r in conn.execute(sql, params)]


def all_watches(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM watches ORDER BY active DESC, ticker, price")]


def trigger_watch(conn, watch_id, on_date):
    """Record a hit and disarm. A named price alert is a one-shot: leaving it
    armed would re-fire every day price sat near the level."""
    conn.execute("UPDATE watches SET triggered_on = ?, active = 0 WHERE id = ?",
                 (on_date, watch_id))
    conn.commit()


def rearm_watch(conn, watch_id):
    conn.execute("UPDATE watches SET active = 1, triggered_on = NULL WHERE id = ?",
                 (watch_id,))
    conn.commit()


def remove_watch(conn, watch_id):
    conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
    conn.commit()
