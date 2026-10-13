"""Incrementally update daily bars for the stored universe.

Run each weekday evening after the US close, before check_alerts.py. This is the
only job that touches the price network, so Yahoo throttling is handled in one
place and every other job reads from SQLite.
"""
import argparse
import time
from collections import Counter
from datetime import date

import bars
import store
from monitor_config import MONITOR


def plan_groups(conn, tickers, cfg, today, force_full=False):
    """Group tickers into at most two fetch plans: full history, and one range.

    Grouping by the exact fetch_plan tuple would split the universe by each
    ticker's own last stored bar, so a fleet whose dates have diverged — some
    added later, some that failed last run — degrades toward one request per
    ticker, defeating the batching the throttle is built around.

    Instead every ticker with history shares the EARLIEST start. Re-fetching a
    few extra days for the others is free, because writes are upserts, and after
    one run every date is aligned again so the range collapses back to the
    overlap window. The fragmentation is self-healing rather than permanent.
    """
    needs_full, ranged = [], []
    for ticker in tickers:
        last = None if force_full else store.last_bar_date(conn, ticker)
        if last is None:
            needs_full.append(ticker)
        else:
            ranged.append((last, ticker))

    groups = []
    if needs_full:
        groups.append((("full", cfg.first_run_period), needs_full))
    if ranged:
        earliest = min(last for last, _ in ranged)
        groups.append((bars.fetch_plan(earliest, today, cfg),
                       [t for _, t in ranged]))
    return groups


def update_universe(conn, tickers, cfg, today, verbose=True, force_full=False):
    """Update every ticker. Returns (status Counter, number that drifted).

    ``force_full`` ignores stored history and re-pulls everything — the monthly
    backstop for any adjustment the overlap window was too short to catch.

    Every ticker contributes exactly one terminal status, so the counter sums to
    len(tickers). Drift is an intermediate state: a drifted ticker's outcome is
    decided by the re-download pass below, and the drift count is reported
    separately rather than double-counted alongside its own resolution.
    """
    results = Counter()
    drifted = []

    for plan, group in plan_groups(conn, tickers, cfg, today, force_full):
        for batch in bars.batches(group, cfg.fetch_batch_size):
            try:
                df = bars.download(batch, plan, cfg)
            except Exception as exc:
                if verbose:
                    print(f"[fail]  {', '.join(batch)}: {exc}")
                results["failed"] += len(batch)
                continue

            for ticker in batch:
                try:
                    outcome = bars.apply_fetch(conn, ticker, df, cfg)
                except Exception as exc:
                    # yfinance silently drops a delisted or renamed ticker from a
                    # multi-ticker frame, which leaves that frame ambiguous for
                    # this name. It must cost one ticker, not abort the run.
                    if verbose:
                        print(f"[fail]  {ticker}: {exc}")
                    results["failed"] += 1
                    continue

                if outcome["status"] == "drift":
                    drifted.append(ticker)
                else:
                    results[outcome["status"]] += 1
            time.sleep(cfg.fetch_pause_seconds)

    # A drifted ticker was re-adjusted upstream; its stored history is stale, so
    # replace it wholesale rather than trying to patch the difference.
    for ticker in drifted:
        if verbose:
            print(f"[drift] {ticker}: re-downloading full history")
        try:
            df = bars.download([ticker], ("full", cfg.first_run_period), cfg)
            rows = bars.frame_to_rows(df, ticker)
        except Exception as exc:
            if verbose:
                print(f"[fail]  {ticker} re-download: {exc}")
            results["failed"] += 1
            continue

        if not rows:
            # Deleting before checking would leave the ticker with no history at
            # all, recorded as a success, and looking brand new on the next run.
            if verbose:
                print(f"[fail]  {ticker}: re-download had no usable bars, "
                      "keeping existing history")
            results["failed"] += 1
            continue

        conn.execute("DELETE FROM bars WHERE ticker = ?", (ticker,))
        conn.commit()
        store.upsert_bars(conn, ticker, rows)
        results["resynced"] += 1
        time.sleep(cfg.fetch_pause_seconds)

    return results, drifted


def main():
    ap = argparse.ArgumentParser(description="Incrementally update daily bars.")
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--tickers", nargs="+", default=None,
                    help="Override the stored universe, for testing")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N tickers")
    ap.add_argument("--resync", action="store_true",
                    help="Re-pull full history for every ticker (monthly backstop)")
    args = ap.parse_args()

    conn = store.init_db(store.connect(args.db))
    tickers = args.tickers or store.active_universe(conn)
    if args.limit:
        tickers = tickers[:args.limit]
    if not tickers:
        print("no tickers — run refresh_universe.py first")
        return

    today = date.today()
    results, drifted = update_universe(conn, tickers, MONITOR, today,
                                       force_full=args.resync)
    succeeded = results["ok"] + results["no_data"] + results["resynced"]

    # check_alerts must not run the shock rule on a ticker whose history was
    # re-adjusted today: a 4:1 split reads as a -75% close-to-close move and
    # would fire a confident, false DROP. Recorded per-day so a later run on a
    # different day is unaffected.
    if drifted:
        store.set_meta(conn, f"resynced_{today.isoformat()}", ",".join(drifted))

    # The timestamp asserts bars are current. Writing it after a run where
    # nothing succeeded would tell every downstream job the data is fresh when
    # not a single bar was written.
    if succeeded:
        store.set_meta(conn, "bars_updated_on", today.isoformat())
        if args.resync:
            store.set_meta(conn, "bars_resynced_on", today.isoformat())

    summary = ", ".join(f"{k}={v}" for k, v in sorted(results.items()))
    print(f"\n{len(tickers)} ticker(s): {summary}"
          + (f"  ({len(drifted)} drifted)" if drifted else ""))

    if not succeeded:
        print("ERROR: every ticker failed — 'bars_updated_on' was NOT updated")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
