"""Replay stored level alerts through the setup rules, without writing anything.

Answers "how many alerts a night would the new rules show?" from the alerts the
monitor already recorded, so it can be run before trusting a rule change.

    python scripts/replay_setups.py --days 10
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_alerts  # noqa: E402
import level_batch  # noqa: E402
import setup_context  # noqa: E402
import timeframes  # noqa: E402
import setups  # noqa: E402
import store  # noqa: E402
from monitor_config import MONITOR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()

    conn = store.connect(args.db)       # only SELECTs below
    days = [r["ts"] for r in conn.execute(
        "SELECT DISTINCT ts FROM alerts ORDER BY ts DESC LIMIT ?", (args.days,))]
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE ts >= ? AND event IN ('touch', 'break') "
        "ORDER BY ts, ticker", (min(days),))]

    bars, majors, trends, daily_levels, tols = {}, {}, {}, {}, {}
    per_day = Counter()
    pairs = Counter()
    examples = {s: [] for s in setups.ORDER}
    for r in rows:
        t = r["ticker"]
        if t not in bars:
            bars[t] = store.read_bars(conn, t)
            majors[t] = {(lv["timeframe"], round(lv["price"], 4)): lv["is_major"]
                         for tf in ("daily", "weekly")
                         for lv in store.read_levels(conn, t, tf)}
            daily_levels[t] = store.read_levels(conn, t, "daily")
            tols[t] = timeframes.config_for(
                "daily", level_batch.frame_for(bars[t], "daily")).sr_touch_tol_pct
        df = bars[t]
        dates = [d.isoformat() for d in df.index.date]
        if r["ts"] not in dates:
            continue
        key = (t, r["ts"])
        pos = dates.index(r["ts"])
        if key not in trends:
            trends[key] = check_alerts.trend_verdicts(df, pos)
        d, w = (v.state for v in trends[key])
        pairs[(d, w)] += 1
        major = majors[t].get((r["timeframe"], round(r["price"], 4)), 0)
        s = check_alerts.tag_event(df, pos, r["event"], r["kind"], r["price"],
                                   major, daily_levels[t], tols[t],
                                   trends[key])["setup"]
        per_day[(r["ts"], s)] += 1
        if s and len(examples[s]) < 12:
            examples[s].append(f"{t}:{r['event']}")

    # Confirmed breakouts: a close through an earlier Breakout setup's price.
    for t in sorted({r["ticker"] for r in rows if r["kind"] == "resistance"}):
        df = bars[t]
        dates = [d.isoformat() for d in df.index.date]
        for day in days:
            if day not in dates:
                continue
            found = check_alerts.confirmed_breakouts(
                conn, t, df, dates.index(day), daily_levels[t], tols[t])
            per_day[(day, setups.CONFIRMED)] += len(found)
            for f in found:
                examples[setups.CONFIRMED].append(
                    f"{t}:{day} (setup {f['setup_ts']}, R {f['price']:,.2f})")

    # Long-range high breakouts are not level alerts: found afresh per ticker.
    for t in store.active_universe(conn):
        df = bars.get(t)
        if df is None:
            df = store.read_bars(conn, t)
        dates = [d.isoformat() for d in df.index.date]
        for day in days:
            if day in dates and setup_context.high_breakout(
                    df, dates.index(day)) is not None:
                per_day[(day, setups.CONFIRMED)] += 1
                examples[setups.CONFIRMED].append(
                    f"{t}:{day} ({setups.LONG_HIGH_SHORT})")

    print(f"{len(rows)} level alerts over {len(days)} days\n")
    print(f"{'day':<12}{'all':>6}" + "".join(f"{setups.LABEL[s]:>20}" for s in setups.ORDER))
    for day in sorted(days):
        total = sum(n for (d, _), n in per_day.items() if d == day)
        print(f"{day:<12}{total:>6}" +
              "".join(f"{per_day[(day, s)]:>20}" for s in setups.ORDER))
    print("\ntrend pairs (daily, weekly) across those alerts:")
    for (d, w), n in pairs.most_common():
        print(f"  {d:<10} {w:<10} {n}")
    for s in setups.ORDER:
        print(f"\n{setups.LABEL[s]} examples: {', '.join(examples[s]) or '-'}")


if __name__ == "__main__":
    main()
