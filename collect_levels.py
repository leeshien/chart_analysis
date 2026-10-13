"""Poll the outstanding level batches and collect each as it finishes.

Replaces a shell watcher that silently gave up after three hours per timeframe
and logged nothing but "finished". Two lessons are built in here: never discard
the error stream, and report progress while waiting, so a long wait is
distinguishable from a stuck one.

Batch turnaround is not predictable — a ten-ticker job settled in 140 seconds
while a 118-ticker job during a degraded period ran past three hours — so the
budget is generous and every poll says what it saw.
"""
import argparse
import time
from datetime import datetime

import level_batch
import refresh_levels
import store
import timeframes
from monitor_config import MONITOR

_DONE = ("SUCCEEDED", "PARTIALLY_SUCCEEDED")
_DEAD = ("FAILED", "CANCELLED", "EXPIRED")


def _stamp():
    return datetime.now().strftime("%H:%M:%S")


def state_of(conn, timeframe):
    """Current job state for a timeframe, or None if nothing is recorded.

    Errors are raised, not swallowed: a watcher that cannot read a state must
    say so rather than quietly treat it as "still running" for hours.
    """
    name = store.get_meta(conn, f"levels_job_{timeframe}")
    if not name:
        return None, None
    job = level_batch.poll(name)
    return name, str(getattr(job.state, "name", job.state))


def watch(conn, frames, interval=60, max_wait=6 * 3600, verbose=True):
    """Poll each timeframe until it settles, collecting on success.

    Returns {timeframe: outcome} where outcome is a collect result dict, or a
    string describing why nothing was collected.
    """
    pending = list(frames)
    results = {}
    deadline = time.time() + max_wait
    last_seen = {}

    while pending and time.time() < deadline:
        for tf in list(pending):
            try:
                name, state = state_of(conn, tf)
            except Exception as exc:
                if verbose:
                    print(f"[{_stamp()}] {tf} poll failed: {exc}")
                continue

            if state is None:
                results[tf] = "no job recorded"
                pending.remove(tf)
                continue

            if state != last_seen.get(tf):
                if verbose:
                    print(f"[{_stamp()}] {tf} {state}")
                last_seen[tf] = state

            if any(k in state for k in _DEAD):
                results[tf] = f"ended {state}"
                pending.remove(tf)
            elif any(k in state for k in _DONE):
                out = refresh_levels.collect(conn, tf, name)
                if verbose:
                    print(f"[{_stamp()}] {tf} collected "
                          f"{out['written']} level(s) across {out['tickers']} ticker(s)")
                    if out.get("empty"):
                        print(f"           parsed to nothing: {len(out['empty'])}")
                results[tf] = out
                pending.remove(tf)

        if pending:
            time.sleep(interval)

    for tf in pending:
        results[tf] = f"still running after {max_wait // 3600}h"
        if verbose:
            print(f"[{_stamp()}] {tf} gave up waiting")
    return results


def main():
    ap = argparse.ArgumentParser(description="Collect level batches as they finish.")
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--timeframes", nargs="+", default=sorted(timeframes.TIMEFRAMES))
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--max-hours", type=float, default=6.0)
    args = ap.parse_args()

    conn = store.init_db(store.connect(args.db))
    results = watch(conn, args.timeframes, args.interval,
                    int(args.max_hours * 3600))

    print()
    for tf, out in results.items():
        if isinstance(out, dict):
            print(f"{tf}: {out['state']} — {out['written']} level(s)")
        else:
            print(f"{tf}: {out}")


if __name__ == "__main__":
    main()
