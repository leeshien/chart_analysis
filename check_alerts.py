"""The daily job: test every stored level against the day's bars and alert.

Run each weekday evening after update_bars.py. Free — no API calls. Reads the
Phase 1 bar store and the Phase 2 levels table.
"""
import argparse
from datetime import date, datetime
from pathlib import Path

import bars as bars_mod
import level_batch
import notify_telegram
import report_pdf
import setup_context
import setups
import store
import timeframes
import touch_detect as td
import trend
from monitor_config import MONITOR

# How stale a timeframe's levels may be before the run says so out loud. A
# monthly weekly-refresh that failed silently would otherwise produce alerts that
# look entirely normal against two-month-old levels.
_STALE_GRACE_DAYS = {"weekly": 40, "daily": 10}


def days_to_check(df, last_checked):
    """Trading days to process, oldest first.

    A missed cron run, a reboot or a long weekend then produces the alerts it
    should have, in order, rather than swallowing them. On a first run only the
    newest day is checked — replaying years of history as fresh alerts would be
    worse than useless.
    """
    if len(df) == 0:
        return []
    dates = [d.isoformat() for d in df.index.date]
    if not last_checked:
        return dates[-1:]
    return [d for d in dates if d > last_checked]


def staleness_warnings(conn, today=None):
    """One warning per timeframe whose levels are past their grace period."""
    today = today or date.today()
    out = []
    for tf in sorted(_STALE_GRACE_DAYS):
        grace = _STALE_GRACE_DAYS[tf]
        newest = store.newest_levels_date(conn, tf)
        if not newest:
            out.append(f"{tf} levels have never been generated")
            continue
        age = (today - datetime.strptime(newest, "%Y-%m-%d").date()).days
        if age > grace:
            out.append(f"{tf} levels are {age} days old (grace {grace})")
    return out


def resynced_today(conn, today):
    """Tickers whose history was re-downloaded for adjustment drift today.

    The shock rule must skip these. Bars are stored auto-adjusted, so a split
    reads as a -75% close-to-close move and would fire a confident, false DROP.
    """
    raw = store.get_meta(conn, f"resynced_{today.isoformat()}", "")
    return {t for t in raw.split(",") if t}


def shock_threshold(df, cfg, bars=250):
    """The close-to-close move that counts as a shock for THIS ticker.

    A flat percentage cannot serve a universe whose median daily bar runs from
    1.48% (DUK) to 9.45% (AAOI) — a 20% day is a multi-year event for one and an
    earnings reaction for the other. Scaling to the ticker's own bar size makes
    "unusual" mean the same thing everywhere, and the floor and cap keep the
    result inside a range a human would still call a shock.
    """
    rng = timeframes.median_bar_range(df.tail(bars))
    if rng <= 0:
        return cfg.shock_pct
    scaled = cfg.shock_range_mult * rng
    return float(min(max(scaled, cfg.shock_min_pct), cfg.shock_max_pct))


def trend_verdicts(df, pos):
    """``(daily, weekly)`` Trend verdicts as of bar ``pos``, from no later bars.

    The weekly series drops a week whose Friday is after ``pos``, so a
    mid-week check reads the completed weeks rather than a stub.
    """
    upto = df.iloc[:pos + 1]
    daily = trend.classify_adaptive(upto, "daily")
    weekly_bars = bars_mod.derive_weekly(upto, today=upto.index[-1].date())
    weekly = trend.classify_adaptive(weekly_bars, "weekly")
    return daily, weekly


def trends_at(df, pos):
    """``(daily, weekly)`` trend states as of bar ``pos``."""
    daily, weekly = trend_verdicts(df, pos)
    return daily.state, weekly.state


def tag_event(df, pos, event, role, price, major, daily_levels, tol, verdicts):
    """The setup fields for one level event: setup, pattern, trends, major.

    ``verdicts`` is ``(daily, weekly)`` Trend objects as of ``pos``. The extra
    context each rule needs is measured only when that rule could apply, since
    the double-bottom search costs a detector run.
    """
    d, w = verdicts
    higher, rise = False, None
    if role == "resistance" and d.state == "sideways" and w.state == "up":
        higher = setup_context.higher_resistance_touched(
            df, pos, price, daily_levels, d.bars, tol)
    if role == "resistance" and d.state == "down" and w.state == "sideways":
        rise = setup_context.rally(df, pos)
    setup = setups.classify(event, role, major, d.state, w.state, higher, rise)
    pattern = (setup_context.reversal_pattern(df, pos, price, tol)
               if setup == setups.BASING else None)
    return {"setup": setup, "pattern": pattern, "daily_trend": d.state,
            "weekly_trend": w.state, "is_major": major}


def confirmed_breakouts(conn, ticker, df, pos, daily_levels, tol, pending=()):
    """Breakout setups from the last CONFIRM_BARS bars that bar ``pos`` closes
    through: ``[{"price", "setup_ts", "timeframe", "is_major"}]``.

    Tested against the setup's own price, not today's level list: a level
    refresh between the setup and the break can move or drop the level (V's
    weekly 381.60 became 385.45 the day after it broke), and the ceiling the
    setup pressed on is the one that matters. A break needs the same close
    through it, with the previous close still under it, that touch_detect
    asks of any resistance.

    Earlier alerts are re-tagged under today's rules rather than read from
    their stored ``setup``, which only exists from 2026-10-08 and was decided
    under older rules before that; only alerts today's bar breaks are
    re-tagged, which keeps this cheap. ``pending`` holds this run's events not
    yet written, so a catch-up run over several days sees them. Setups whose
    prices sit inside two touch bands of each other are one ceiling, reported
    once at its most recent setup.
    """
    dates = [d.isoformat() for d in df.index.date]
    start, day = dates[max(0, pos - setups.CONFIRM_BARS)], dates[pos]
    prior = [dict(r) for r in conn.execute(
        "SELECT ts, timeframe, event, price, is_major FROM alerts "
        "WHERE ticker = ? AND kind = 'resistance' AND event IN ('touch', 'break') "
        "AND ts >= ? AND ts < ?", (ticker, start, day))]
    prior += [e for e in pending
              if e.get("kind") == "resistance" and e.get("price") is not None
              and e["event"] in ("touch", "break") and start <= e["ts"] < day]

    bar = df.iloc[pos]
    shaped = {"Low": float(bar["Low"]), "High": float(bar["High"]),
              "Close": float(bar["Close"])}
    prev_close = float(df["Close"].iloc[pos - 1])
    found = []
    for p in sorted(prior, key=lambda p: p["ts"], reverse=True):
        if p["ts"] not in dates or any(
                abs(p["price"] - f["price"]) <= 2.0 * tol * f["price"]
                for f in found):
            continue
        if (td.role_of(p["price"], prev_close) != "resistance"
                or td.classify(shaped, prev_close, p["price"], tol) != "break"):
            continue
        ppos = dates.index(p["ts"])
        tagged = tag_event(df, ppos, p["event"], "resistance", p["price"],
                           int(bool(p.get("is_major"))), daily_levels, tol,
                           trend_verdicts(df, ppos))
        if tagged["setup"] == setups.BREAKOUT:
            found.append({"price": p["price"], "setup_ts": p["ts"],
                          "timeframe": p["timeframe"],
                          "is_major": int(bool(p.get("is_major")))})
    return found


def apply_confirmations(events, found, day, tol, base):
    """Mark the day's events that confirm a setup, adding any that are missing.

    A break at a level still in the list is upgraded in place; a setup price
    no longer stored gets an event of its own, built on ``base`` (ticker,
    close, trends).
    """
    for c in found:
        match = next((e for e in events
                      if e["ts"] == day and e["ticker"] == base["ticker"]
                      and e.get("kind") == "resistance"
                      and e.get("price") is not None and e["event"] == "break"
                      and abs(e["price"] - c["price"]) <= 2.0 * tol * c["price"]),
                     None)
        if match is not None:
            match.update(setup=setups.CONFIRMED, setup_ts=c["setup_ts"])
            continue
        events.append({**base, "ts": day, "timeframe": c["timeframe"],
                       "price": c["price"], "kind": "resistance",
                       "event": "break", "confluence": 0, "score": 1.0,
                       "delivered": 0, "setup": setups.CONFIRMED,
                       "setup_ts": c["setup_ts"], "pattern": None,
                       "is_major": c["is_major"]})


def resolve_outlooks(rows):
    """One verdict per stock per day: settle a Bullish/Bearish conflict by the
    weekly trend. Mutates ``rows``, one ticker's events for one day.

    Each level is judged on its own, so one bar can touch a lower support
    (Pullback, Bullish) after breaking a higher one (Major support test,
    Bearish) - HPQ on 2026-10-09. The weekly trend is the bias that settles it:

    - up: the Bullish reading stands; down: the Bearish one does. The losing
      rows keep their record but lose their setup, and the strongest winner
      carries them as a ``note`` so the other level is still seen.
    - sideways: a range, where both levels are in play. Every row stays and
      the stock is listed under Sideways (``outlook``).
    - undecided: no bias to settle it with, so nothing is shown.

    Only conflicts are settled here; every other alert keeps its setup's own
    outlook.
    """
    tagged = [r for r in rows if r.get("setup")]
    if len({setups.OUTLOOK[r["setup"]] for r in tagged}) < 2:
        return
    weekly = tagged[0].get("weekly_trend")
    if weekly == "sideways":
        for r in tagged:
            r["outlook"] = setups.SIDEWAYS
        return
    if weekly not in ("up", "down"):
        for r in tagged:
            r.update(setup=None, pattern=None)
        return
    keep = setups.BULLISH if weekly == "up" else setups.BEARISH
    winners = sorted((r for r in tagged if setups.OUTLOOK[r["setup"]] == keep),
                     key=lambda r: setups.rank(r["setup"]))
    losers = [r for r in tagged if setups.OUTLOOK[r["setup"]] != keep]
    winners[0]["note"] = "; ".join(
        f"also {notify_telegram.level_phrase(r)}" for r in losers)
    for r in losers:
        r.update(setup=None, pattern=None)


def is_deliverable(row) -> bool:
    """Only the three setups, and the user's own watches, reach the user."""
    return bool(row.get("setup")) or notify_telegram.is_watch(row)


def check_watches(conn, ticker, df, dates, days, tol_pct):
    """User-named price alerts for one ticker.

    Separate from the Gemini levels: these are prices the user asked about, so
    they fire once and disarm rather than relying on visit suppression. Leaving
    one armed would re-fire every day price sat near the level.

    Detection reuses the same side-aware rules as the levels, with the band
    scaled to the ticker's own volatility — a 1% window on AXTI, whose median
    daily bar is 12%, would be stepped straight over in a single gap.
    """
    watches = store.active_watches(conn, ticker)
    if not watches:
        return []

    events = []
    for day in days:
        pos = dates.index(day)
        if pos == 0:
            continue
        bar = df.iloc[pos]
        prev_close = float(df["Close"].iloc[pos - 1])
        close = float(bar["Close"])
        shaped = {"Low": float(bar["Low"]), "High": float(bar["High"]),
                  "Close": close}

        for w in watches:
            if not w["active"]:
                continue
            event = td.classify(shaped, prev_close, w["price"], tol_pct)
            if not event:
                continue
            store.trigger_watch(conn, w["id"], day)
            w["active"] = 0
            events.append({"ts": day, "ticker": ticker, "timeframe": "watch",
                           "price": w["price"],
                           "kind": td.role_of(w["price"], prev_close),
                           "event": event, "title": w["title"],
                           "confluence": 0, "score": 1.0,
                           "close": close, "delivered": 0})
    return events


def check_ticker(conn, ticker, cfg_by_tf, levels_by_tf, last_checked,
                 skip_shock=False):
    """Every event for one ticker across the days it has not been checked for.

    Returns ``(events, newest_bar_date)``.
    """
    df = store.read_bars(conn, ticker)
    if len(df) < 2:
        return [], None

    dates = [d.isoformat() for d in df.index.date]
    shock_pct = shock_threshold(df, MONITOR)
    days = days_to_check(df, last_checked)

    # Tolerance is volatility-relative, so it must be derived from THIS ticker
    # frame. The shared cfg_by_tf is built without one and silently falls back
    # to the flat percentage, which is what this was doing until now.
    #
    # Every level, weekly ones included, is tested against a DAILY bar, so the
    # band is the daily one. The weekly band is sized to a weekly bar's range
    # (2-3x wider) and let a day whose low stayed 1.7% above a weekly support
    # count as touching it.
    tol = timeframes.config_for(
        "daily", level_batch.frame_for(df, "daily")).sr_touch_tol_pct

    events = check_watches(conn, ticker, df, dates, days, tol)

    # Trends cost a few regressions each, so they are measured only on a day
    # that produced a level event, and once per day.
    trend_cache = {}

    def verdicts(pos):
        if pos not in trend_cache:
            trend_cache[pos] = trend_verdicts(df, pos)
        return trend_cache[pos]

    for day in days_to_check(df, last_checked):
        pos = dates.index(day)
        if pos == 0:
            continue
        bar = df.iloc[pos]
        prev_close = float(df["Close"].iloc[pos - 1])
        close = float(bar["Close"])
        shaped = {"Low": float(bar["Low"]), "High": float(bar["High"]),
                  "Close": close}

        if not skip_shock:
            kind = td.shock(close, prev_close, shock_pct)
            if kind:
                events.append({"ts": day, "ticker": ticker, "timeframe": None,
                               "price": None, "kind": None, "event": kind,
                               "confluence": 0, "score": close / prev_close - 1.0,
                               "close": close, "delivered": 0})

        # A close above the long-range weekly high alerts in any trend.
        peak = setup_context.high_breakout(df, pos)
        if peak is not None:
            d, w = verdicts(pos)
            events.append({"ts": day, "ticker": ticker, "timeframe": "weekly",
                           "price": peak, "kind": "resistance", "event": "break",
                           "confluence": 0, "score": 1.0, "close": close,
                           "delivered": 0, "setup": setups.CONFIRMED,
                           "pattern": setups.LONG_HIGH,
                           "daily_trend": d.state,
                           "weekly_trend": w.state, "is_major": 1})

        for tf, rows in levels_by_tf.items():
            other = levels_by_tf["weekly" if tf == "daily" else "daily"]
            for lv in rows:
                event = td.classify(shaped, prev_close, lv["price"], tol)
                if not event:
                    continue
                role = td.role_of(lv["price"], prev_close)

                if event == "touch":
                    if store.find_open_visit(conn, ticker, tf, lv["price"], tol):
                        store.extend_visit(conn, ticker, tf, lv["price"], day, tol)
                        continue
                    store.open_visit(conn, ticker, tf, lv["price"], role, day)

                confl = td.has_confluence(lv["price"], role, other,
                                          MONITOR.confluence_pct)
                major = int(bool(lv.get("is_major")))
                row = {"ts": day, "ticker": ticker, "timeframe": tf,
                               "price": lv["price"], "kind": role, "event": event,
                               "confluence": int(confl), "score": lv["score"],
                               "close": close, "delivered": 0,
                               **tag_event(df, pos, event, role, lv["price"],
                                           major, levels_by_tf["daily"], tol,
                                           verdicts(pos))}
                events.append(row)

        # A close through the resistance of an earlier Breakout setup.
        found = confirmed_breakouts(conn, ticker, df, pos, levels_by_tf["daily"],
                                    tol, events)
        if found:
            d, w = verdicts(pos)
            apply_confirmations(events, found, day, tol,
                                {"ticker": ticker, "close": close,
                                 "daily_trend": d.state, "weekly_trend": w.state})

        resolve_outlooks([e for e in events if e["ts"] == day])

    return events, dates[-1]


def chart_for(conn, ticker, day=None):
    """Everything one ticker's side-by-side chart page needs, or None.

    Both timeframes' levels, the daily bars (the weekly ones are derived from
    them), and the trend verdicts as of ``day`` (the alert's day; default the
    newest bar). The verdicts MUST be the ones the setup was decided on: a
    Friday bar completes a week, so the weekly verdict on Friday can differ
    from Thursday's and contradict the alert's label.
    """
    daily_rows = store.read_levels(conn, ticker, "daily")
    weekly_rows = store.read_levels(conn, ticker, "weekly")
    bars_df = store.read_bars(conn, ticker)
    if not (daily_rows or weekly_rows) or len(bars_df) < 2:
        return None
    dates = [d.isoformat() for d in bars_df.index.date]
    pos = dates.index(day) if day in dates else len(bars_df) - 1
    daily_tr, weekly_tr = trend_verdicts(bars_df, pos)
    return {"bars": bars_df, "daily_levels": daily_rows,
            "weekly_levels": weekly_rows,
            "trends": {"daily": daily_tr, "weekly": weekly_tr},
            "as_of": dates[pos]}


def main():
    ap = argparse.ArgumentParser(description="Daily level and shock check.")
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--tickers", nargs="+", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="output_alerts")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect and build the PDF, send nothing")
    ap.add_argument("--bootstrap", action="store_true",
                    help="Record visits and mark the backlog delivered without "
                         "sending; use once, on the first ever run")
    args = ap.parse_args()

    conn = store.init_db(store.connect(args.db))
    tickers = args.tickers or store.active_universe(conn)
    if args.limit:
        tickers = tickers[:args.limit]

    today = date.today()
    warnings = staleness_warnings(conn, today)
    cfg_by_tf = {tf: timeframes.config_for(tf) for tf in timeframes.TIMEFRAMES}
    last_checked = store.get_meta(conn, "last_checked_date")
    skip = resynced_today(conn, today)

    all_events, newest = [], last_checked
    for ticker in tickers:
        levels_by_tf = {tf: store.read_levels(conn, ticker, tf)
                        for tf in timeframes.TIMEFRAMES}
        events, last_bar = check_ticker(conn, ticker, cfg_by_tf, levels_by_tf,
                                        last_checked, ticker in skip)
        for e in events:
            store.record_alert(conn, e)
        all_events += events
        if last_bar and (newest is None or last_bar > newest):
            newest = last_bar

    store.close_visits_before(conn, today.isoformat(), MONITOR.visit_gap_days)
    if newest:
        store.set_meta(conn, "last_checked_date", newest)

    # Every event is recorded, but only the three setups and the user's
    # watches are shown. A plain touch of a minor level is 100+ lines a night
    # that say nothing about what to trade.
    shown = [e for e in all_events if is_deliverable(e)]

    # Charts render only for tickers that alerted: at 1-2s each, rendering all
    # 503 every evening would cost 20 minutes for pages nobody opens.
    alerted = sorted({e["ticker"] for e in shown})
    newest = {}
    for e in shown:
        newest[e["ticker"]] = max(newest.get(e["ticker"], ""), e["ts"])
    charts = {}
    for ticker in alerted:
        c = chart_for(conn, ticker, newest[ticker])
        if c:
            charts[ticker] = c

    # A re-run on the same day finds fewer events (the first run opened visits),
    # so writing to one dated filename would overwrite a full report with an
    # empty one. Only replace the day's report when this run found more.
    pdf_path = Path(args.out) / f"alerts_{today.isoformat()}.pdf"
    if shown or not pdf_path.exists():
        pdf = report_pdf.build(pdf_path, shown, charts, today.isoformat(),
                               warnings, store.universe_rows(conn),
                               scanned=sum(e.get("price") is not None
                                           for e in all_events))
    else:
        pdf = pdf_path
        print("no new setups; keeping the existing report")
    counts = {s: sum(e.get("setup") == s for e in shown) for s in setups.ORDER}
    print(f"{len(all_events)} event(s), {len(shown)} shown across "
          f"{len(alerted)} ticker(s) -> {pdf}")
    print("  " + ", ".join(f"{setups.LABEL[s]}: {n}" for s, n in counts.items()))
    for w in warnings:
        print(f"[stale] {w}")

    if args.dry_run:
        print("dry run: nothing sent")
        return

    # Everything pending is marked delivered at the end, but only setups and
    # watches are sent.
    pending = store.undelivered_alerts(conn)
    to_send = [p for p in pending if is_deliverable(p)]

    if args.bootstrap:
        # The first run finds every level already touched and reports the current
        # state of the whole index — 321 rows on this database. Those are not
        # events that happened today, and sending them would be wrong in kind,
        # not merely in volume. Record the visits, mark the backlog delivered,
        # and let tomorrow's run alert on genuine changes.
        store.mark_delivered(conn, [p["id"] for p in pending])
        print(f"bootstrap: {len(pending)} backlog alert(s) marked delivered, "
              "nothing sent")
        return

    # Watches are pushed outside the cap. The user named these six prices and
    # they fire once each, ever — dropping one because the same evening happened
    # to produce five confluence touches would defeat the point of asking.
    watches = [p for p in to_send if notify_telegram.is_watch(p)]
    for row in watches:
        notify_telegram.send_message(notify_telegram.format_alert(row))

    # Breakouts and breakout setups are the project's best entry signals, so
    # each such name is pushed on its own, breakouts first. The
    # pushes are capped: a broad rally can put dozens of names at resistance
    # at once, and a phone that buzzes ninety times is a phone that gets
    # silenced. The remainder still lead the digest and the PDF.
    setup_rows = [p for p in to_send if not notify_telegram.is_watch(p)]
    session = max((p["ts"] for p in to_send), default=today.isoformat())
    pushed, urgent = notify_telegram.deliver_setups(
        setup_rows, store.universe_rows(conn), session, pdf,
        MONITOR.max_urgent_push, MONITOR.digest_limit)
    store.mark_delivered(conn, [p["id"] for p in pending])
    print(f"sent {len(watches)} watch + {pushed} urgent (of {urgent}) "
          "+ digest + report")


if __name__ == "__main__":
    main()
