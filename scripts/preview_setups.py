"""Render one past day's alerts under the setup rules, without sending or storing.

Re-tests the level alerts already recorded for ``--day`` against the daily-bar
band, re-tags them with their setup and trends, then writes the PDF report and
prints the Telegram digest exactly as check_alerts.py would build them. Nothing
is written to the database.

    python scripts/preview_setups.py --day 2026-10-07 --charts

``--send`` also delivers it to Telegram the way check_alerts.py does: each
urgent setup pushed on its own (capped), then the digest, then the PDF.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_alerts  # noqa: E402
import level_batch  # noqa: E402
import notify_telegram  # noqa: E402
import report_pdf  # noqa: E402
import setup_context  # noqa: E402
import setups  # noqa: E402
import store  # noqa: E402
import timeframes  # noqa: E402
import touch_detect as td  # noqa: E402
from monitor_config import MONITOR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--day", required=True)
    ap.add_argument("--out", default="output_alerts")
    ap.add_argument("--charts", action="store_true",
                    help="Include a chart page per ticker (slower)")
    ap.add_argument("--send", action="store_true",
                    help="Send the pushes, digest and PDF to Telegram")
    args = ap.parse_args()

    conn = store.connect(args.db)       # only reads below
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE ts = ? AND event IN ('touch', 'break')",
        (args.day,))]

    # Stored alerts were detected with whatever band was live that night, so
    # re-test each against the daily-bar band check_alerts uses now. Weekly
    # levels used to get the wider weekly band; some of those drop out here.
    retested, dropped, before = [], [], Counter()
    for r in rows:
        df = store.read_bars(conn, r["ticker"])
        dates = [d.isoformat() for d in df.index.date]
        pos = dates.index(args.day)
        before[r.get("setup")] += 1
        tol = timeframes.config_for(
            "daily", level_batch.frame_for(df, "daily")).sr_touch_tol_pct
        bar = df.iloc[pos]
        event = td.classify({"Low": float(bar["Low"]), "High": float(bar["High"]),
                             "Close": float(bar["Close"])},
                            float(df["Close"].iloc[pos - 1]), r["price"], tol)
        if event is None:
            dropped.append(f"{r['ticker']} {r['timeframe'][0].upper()} "
                           f"{r['price']:,.2f}")
            continue
        r["event"] = event
        major = next((lv["is_major"] for lv in
                      store.read_levels(conn, r["ticker"], r["timeframe"])
                      if abs(lv["price"] - r["price"]) < 1e-6), 0)
        daily_levels = store.read_levels(conn, r["ticker"], "daily")
        r.update(check_alerts.tag_event(
            df, pos, event, r["kind"], r["price"], major, daily_levels, tol,
            check_alerts.trend_verdicts(df, pos)))
        retested.append(r)

    # Confirmed breakouts: any stock with a resistance alert in the setup
    # window may close through an earlier Breakout setup today, whether or not
    # a level alert was stored for it today.
    window_start = conn.execute(
        "SELECT MIN(ts) FROM (SELECT DISTINCT ts FROM alerts WHERE ts < ? "
        "ORDER BY ts DESC LIMIT ?)", (args.day, setups.CONFIRM_BARS)).fetchone()[0]
    for (t,) in conn.execute(
            "SELECT DISTINCT ticker FROM alerts WHERE kind = 'resistance' "
            "AND ts >= ? AND ts < ?", (window_start or args.day, args.day)):
        df = store.read_bars(conn, t)
        dates = [d.isoformat() for d in df.index.date]
        if args.day not in dates:
            continue
        pos = dates.index(args.day)
        tol = timeframes.config_for(
            "daily", level_batch.frame_for(df, "daily")).sr_touch_tol_pct
        found = check_alerts.confirmed_breakouts(
            conn, t, df, pos, store.read_levels(conn, t, "daily"), tol)
        if found:
            d, w = check_alerts.trend_verdicts(df, pos)
            check_alerts.apply_confirmations(
                retested, found, args.day, tol,
                {"ticker": t, "close": float(df["Close"].iloc[pos]),
                 "daily_trend": d.state, "weekly_trend": w.state})
    for t in {r["ticker"] for r in retested}:
        check_alerts.resolve_outlooks([r for r in retested if r["ticker"] == t])
    shown = [r for r in retested if r.get("setup")]

    # Long-range high breakouts are not level alerts, so they are found afresh.
    for t in store.active_universe(conn):
        df = store.read_bars(conn, t)
        dates = [d.isoformat() for d in df.index.date]
        if args.day not in dates:
            continue
        pos = dates.index(args.day)
        peak = setup_context.high_breakout(df, pos)
        if peak is None:
            continue
        d, w = check_alerts.trend_verdicts(df, pos)
        shown.append({"ts": args.day, "ticker": t, "timeframe": "weekly",
                      "price": peak, "kind": "resistance", "event": "break",
                      "confluence": 0, "score": 1.0,
                      "close": float(df["Close"].iloc[pos]),
                      "setup": setups.CONFIRMED, "pattern": setups.LONG_HIGH,
                      "daily_trend": d.state, "weekly_trend": w.state,
                      "is_major": 1})

    universe_rows = store.universe_rows(conn)
    charts = {}
    if args.charts:
        for t in sorted({r["ticker"] for r in shown}):
            c = check_alerts.chart_for(conn, t, args.day)
            if c:
                charts[t] = c

    pdf = report_pdf.build(Path(args.out) / f"preview_setups_{args.day}.pdf",
                           shown, charts, args.day, [], universe_rows,
                           scanned=len(rows))
    after = Counter(r["setup"] for r in shown)
    print(f"{len(rows)} level alerts -> {len(shown)} shown -> {pdf}")
    print(f"dropped by the daily-bar band: {len(dropped)}  "
          f"({', '.join(dropped[:15])}{' ...' if len(dropped) > 15 else ''})")
    print("setup                 as sent   now")
    for s in setups.ORDER:
        print(f"  {setups.LABEL[s]:<20}{before[s]:>7}{after[s]:>6}")
    print()
    print(notify_telegram.format_setup_digest(shown, universe_rows, args.day,
                                              MONITOR.digest_limit))
    for r in shown:
        if r.get("pattern"):
            print(f"pattern: {r['ticker']} {setups.display(r)}")
    for _, rs in sorted(notify_telegram.by_ticker(
            [r for r in shown if r["setup"] in setups.URGENT]),
            key=lambda tr: setups.rank(tr[1][0]["setup"])):
        print("\n--- push ---\n" + notify_telegram.format_push(rs, universe_rows))

    if not args.send:
        return
    pushed, urgent = notify_telegram.deliver_setups(
        shown, universe_rows, args.day, pdf, MONITOR.max_urgent_push,
        MONITOR.digest_limit)
    print(f"\nsent {pushed} push(es) (of {urgent}) + digest + report")


if __name__ == "__main__":
    main()
