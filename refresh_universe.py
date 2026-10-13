"""Refresh the stored S&P 500 constituent list. Run monthly, before level refresh."""
import argparse
from datetime import date

import store
import universe
from monitor_config import MONITOR


def refresh(conn, html, today, min_constituents=None):
    """Apply a fetched page to the store. Returns (added, removed).

    Raises ValueError if the parse yielded implausibly few names. Without that
    guard a structural change to the page — the symbol column moving from <td>
    to <th>, say — would parse to an empty list, remove nothing (diff_universe
    refuses to act on an empty fetch), stamp the refresh as successful and print
    "no changes". The monitor would then run on a frozen universe indefinitely
    while reporting health. A loud failure is the only safe outcome here.
    """
    pairs = universe.parse_constituents(html)
    fetched = [p[0] for p in pairs]

    floor = MONITOR.min_constituents if min_constituents is None else min_constituents
    if len(fetched) < floor:
        raise ValueError(
            f"parsed only {len(fetched)} constituents, expected at least {floor}; "
            "the page structure has probably changed — refusing to record a refresh")

    # Scope the diff to index members: a chain-only ticker is not in Wikipedia
    # and must not be retired as though it had left the S&P.
    added, removed = universe.diff_universe(
        store.active_universe(conn, source='sp500'), fetched)
    store.add_constituents(conn, pairs, today.isoformat())
    if removed:
        store.remove_constituents(conn, removed, today.isoformat())
    store.set_meta(conn, "universe_refreshed_on", today.isoformat())
    return added, removed


def main():
    ap = argparse.ArgumentParser(description="Refresh the S&P 500 constituent list.")
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--url", default=MONITOR.sp500_url)
    args = ap.parse_args()

    conn = store.init_db(store.connect(args.db))
    html = universe.fetch_html(args.url)
    added, removed = refresh(conn, html, date.today())

    print(f"universe: {len(store.active_universe(conn))} active")
    if added:
        print(f"  added:   {', '.join(added)}")
    if removed:
        print(f"  removed: {', '.join(removed)}")
    if not added and not removed:
        print("  no changes")


if __name__ == "__main__":
    main()
