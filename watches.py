"""Manage user-named price watches: add, list, re-arm, remove.

A watch is a price the user asked about directly, independent of the levels
Gemini finds. It fires once and disarms, so it needs a way to be re-armed;
that is what this command is for.

    python watches.py add ICE 124 CME 220 --title "Monitoring Double Bottom"
    python watches.py list
    python watches.py rearm 3
    python watches.py remove 3
"""
import argparse
from datetime import date

import store
import timeframes
from monitor_config import MONITOR


def parse_pairs(tokens):
    """``["ICE", "124", "CME", "220"]`` -> ``[("ICE", 124.0), ("CME", 220.0)]``.

    Taking the arguments positionally keeps the common case — arming several
    prices at once — to one line, which is how the request arrives in practice.
    """
    if len(tokens) % 2:
        raise SystemExit("expected TICKER PRICE pairs; got an odd number of "
                         f"arguments: {tokens}")
    out = []
    for i in range(0, len(tokens), 2):
        ticker = tokens[i].upper()
        try:
            price = float(tokens[i + 1])
        except ValueError:
            raise SystemExit(f"{tokens[i + 1]!r} is not a price "
                             f"(after {ticker})")
        if price <= 0:
            raise SystemExit(f"{ticker}: price must be positive, got {price}")
        out.append((ticker, price))
    return out


def describe(conn, ticker, price):
    """Where the target sits relative to the last close, and the alert band.

    Printed on add so a typo — a target above price when a support was meant,
    or a decimal in the wrong place — is visible immediately rather than on the
    evening it fails to fire.
    """
    df = store.read_bars(conn, ticker)
    if len(df) == 0:
        return "no bars stored - will never fire until this ticker is tracked"

    close = float(df["Close"].iloc[-1])
    gap = price / close - 1.0
    tol = timeframes.config_for("daily", df.tail(260)).sr_touch_tol_pct
    side = "support" if price < close else "resistance"
    return (f"close {close:,.2f}  target {gap:+.1%} ({side})  "
            f"band +-{tol:.2%} ({price * (1 - tol):,.2f}-{price * (1 + tol):,.2f})")


def cmd_add(conn, args):
    pairs = parse_pairs(args.pairs)
    today = date.today().isoformat()
    for ticker, price in pairs:
        wid = store.add_watch(conn, ticker, price, args.title, args.note, today)
        print(f"#{wid} {ticker} @ {price:,.2f}   {describe(conn, ticker, price)}")
    print(f"{len(pairs)} watch(es) armed"
          + (f' under "{args.title}"' if args.title else ""))


def cmd_list(conn, args):
    rows = store.all_watches(conn) if args.all else store.active_watches(conn)
    if not rows:
        print("no watches" + ("" if args.all else " armed"))
        return
    for r in rows:
        state = "armed" if r["active"] else f"hit {r['triggered_on']}"
        print(f"#{r['id']:<4} {r['ticker']:<6} {r['price']:>10,.2f}  "
              f"{state:<16} {r['title'] or ''}")


def cmd_rearm(conn, args):
    for wid in args.ids:
        store.rearm_watch(conn, wid)
    print(f"re-armed {len(args.ids)} watch(es)")


def cmd_remove(conn, args):
    for wid in args.ids:
        store.remove_watch(conn, wid)
    print(f"removed {len(args.ids)} watch(es)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(MONITOR.db_path))
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="arm TICKER PRICE pairs")
    a.add_argument("pairs", nargs="+", metavar="TICKER PRICE")
    a.add_argument("--title", default=None, help="label carried into the alert")
    a.add_argument("--note", default=None)
    a.set_defaults(fn=cmd_add)

    l = sub.add_parser("list", help="show watches")
    l.add_argument("--all", action="store_true", help="include triggered ones")
    l.set_defaults(fn=cmd_list)

    r = sub.add_parser("rearm", help="re-arm triggered watches by id")
    r.add_argument("ids", nargs="+", type=int)
    r.set_defaults(fn=cmd_rearm)

    d = sub.add_parser("remove", help="delete watches by id")
    d.add_argument("ids", nargs="+", type=int)
    d.set_defaults(fn=cmd_remove)

    args = ap.parse_args()
    conn = store.init_db(store.connect(args.db))
    args.fn(conn, args)


if __name__ == "__main__":
    main()
