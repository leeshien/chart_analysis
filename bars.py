import time
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

import store


_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def derive_weekly(daily, drop_partial=True, today=None):
    """Weekly OHLCV from daily bars, week ending Friday.

    Weekly bars are derived rather than stored: they are a pure function of the
    daily series, and keeping a second store would create something that can
    disagree with the first.

    ``drop_partial`` removes a final bucket whose Friday has not arrived yet.
    Without it, a job running on Wednesday computes levels against a three-day
    "week" that looks entirely ordinary.
    """
    if len(daily) == 0:
        return daily
    weekly = daily.resample("W-FRI").agg(_AGG).dropna(how="any")
    if drop_partial and len(weekly) > 0:
        cutoff = today or date.today()
        if weekly.index[-1].date() > cutoff:
            weekly = weekly.iloc[:-1]
    return weekly


def fetch_plan(last_stored_date, today, cfg):
    """What to request for one ticker.

    ``("full", period)`` for a ticker with no history, else
    ``("range", start, end)`` covering an overlap window plus everything new.

    The range starts before the last stored bar so the overlap can be compared
    for retroactive adjustment, and ends at today + 1 because yfinance treats
    ``end`` as exclusive. Starting from the last stored date rather than
    "yesterday" means a multi-day outage backfills in a single request.
    """
    if not last_stored_date:
        return ("full", cfg.first_run_period)

    last = datetime.strptime(last_stored_date, "%Y-%m-%d").date()
    start = last - timedelta(days=cfg.fetch_overlap_days)
    end = today + timedelta(days=1)
    return ("range", start.isoformat(), end.isoformat())


def detect_drift(stored_closes, fetched_closes, cfg) -> bool:
    """True if any overlapping date's close disagrees beyond tolerance.

    Bars are stored auto-adjusted, so every split and dividend rewrites the whole
    history. Comparing an overlap window catches that without modelling corporate
    actions at all: if old rows changed, something was re-adjusted, and the
    ticker's stored history is now inconsistent with the provider's.

    Comparison is relative, because a 0.5% dividend adjustment on a $900 stock is
    $4.50 while on a $9 stock it is 4.5 cents — an absolute threshold would either
    miss one or fire constantly on the other.
    """
    for day, fetched in fetched_closes.items():
        stored = stored_closes.get(day)
        if stored is None:
            continue
        denominator = max(abs(stored), 1e-9)
        if abs(stored - fetched) / denominator > cfg.drift_rel_tolerance:
            return True
    return False


_FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def _flatten(df, ticker=None):
    """Reduce a yfinance frame to flat OHLCV columns for one ticker.

    yfinance has moved the ticker between MultiIndex levels across releases, and
    a single-ticker request can come back either flat or as a one-ticker
    MultiIndex. So both levels are searched rather than trusting a fixed
    position, and the field level is identified by actually containing OHLCV
    names. Anything ambiguous raises: silently collapsing a multi-ticker frame
    would yield duplicate Open/Open/High/High columns and corrupt every bar
    written from it, with nothing to signal that it happened.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    if ticker is not None:
        for level in (1, 0):
            if ticker in df.columns.get_level_values(level):
                return df.xs(ticker, axis=1, level=level)

    field_level = None
    for level in (0, 1):
        if set(_FIELDS) & set(df.columns.get_level_values(level)):
            field_level = level
            break
    if field_level is None:
        raise ValueError(f"no OHLCV level in columns: {list(df.columns)[:6]}")

    flat = df.copy()
    flat.columns = df.columns.get_level_values(field_level)
    if flat.columns.duplicated().any():
        raise ValueError(
            "several tickers in the frame but none named for flattening: "
            f"{list(df.columns)[:6]}")
    return flat


def frame_to_rows(df, ticker=None) -> list:
    """(date, open, high, low, close, volume) tuples, skipping incomplete bars.

    A bar missing any field is dropped rather than stored with a null: every
    downstream calculation assumes all five values are present.
    """
    if df is None or len(df) == 0:
        return []
    df = _flatten(df, ticker)
    if not set(_FIELDS) <= set(df.columns):
        return []

    rows = []
    for stamp, row in df[_FIELDS].dropna(how="any").iterrows():
        rows.append((pd.Timestamp(stamp).date().isoformat(),
                     float(row["Open"]), float(row["High"]), float(row["Low"]),
                     float(row["Close"]), float(row["Volume"])))
    return rows


def frame_closes(df, ticker=None) -> dict:
    """{iso_date: close} from a fetched frame, for the drift comparison."""
    return {r[0]: r[4] for r in frame_to_rows(df, ticker)}


def apply_fetch(conn, ticker, df, cfg) -> dict:
    """Compare a fetched frame against the store, then write or flag drift.

    Returns ``{"status": ..., "written": n}`` where status is one of:
      ok      — bars written
      no_data — the provider returned nothing (holiday, or nothing new)
      drift   — the overlap disagrees, so this ticker needs a full re-download
                and nothing is written in the meantime
    """
    fetched = frame_closes(df, ticker)
    if not fetched:
        return {"status": "no_data", "written": 0}

    earliest = min(fetched)
    stored = store.closes_by_date(conn, ticker, since=earliest)
    if detect_drift(stored, fetched, cfg):
        return {"status": "drift", "written": 0}

    written = store.upsert_bars(conn, ticker, frame_to_rows(df, ticker))
    return {"status": "ok", "written": written}


def download(tickers, plan, cfg, timeout=30):
    """Fetch one batch. ``plan`` is a fetch_plan tuple shared by the batch.

    The only network call in this module. Kept trivial so everything around it
    stays testable without a connection.
    """
    kwargs = dict(interval="1d", auto_adjust=True, progress=False,
                  timeout=timeout, group_by="column")
    if plan[0] == "full":
        kwargs["period"] = plan[1]
    else:
        kwargs["start"], kwargs["end"] = plan[1], plan[2]

    last_error = None
    for attempt in range(1, cfg.fetch_max_retries + 1):
        try:
            df = yf.download(list(tickers), **kwargs)
            if df is not None and len(df) > 0:
                return df
        except Exception as exc:            # network / 429 / proxy
            last_error = exc
        if attempt < cfg.fetch_max_retries:
            time.sleep(cfg.fetch_pause_seconds * attempt * 2)
    if last_error:
        raise last_error
    return None


def batches(items, size):
    """Split a list into fixed-size chunks."""
    return [items[i:i + size] for i in range(0, len(items), size)]
