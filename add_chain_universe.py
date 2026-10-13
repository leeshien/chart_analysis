"""Add the AI-chain tickers to the monitored universe and backfill classification.

The S&P constituents arrive from Wikipedia with a GICS sector. The chain names
do not, so their sector and market cap come from yfinance — whose sector labels
differ from GICS ("Technology" rather than "Information Technology"), and are
normalised here so one taxonomy reaches the digest.

Run after refresh_universe.py. Safe to re-run: it updates in place.
"""
import argparse
import time
from datetime import date

import yfinance as yf

import ai_chain
import store
from monitor_config import MONITOR

# yfinance uses its own sector names. Map them onto GICS so alerts group under
# one taxonomy rather than two that nearly agree.
_SECTOR_MAP = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Healthcare": "Health Care",
    "Basic Materials": "Materials",
}


def normalise_sector(raw):
    if not raw:
        return None
    return _SECTOR_MAP.get(raw, raw)


def primary_tier(ticker):
    """The tier a ticker is reported under. Several sit in more than one;
    the earliest-numbered wins so a name appears once, upstream-most."""
    tiers = ai_chain.tier_of(ticker)
    return tiers[0] if tiers else None


def enrich(conn, tickers, pause=0.4, verbose=True):
    """Fetch sector and market cap per ticker and write them to the universe."""
    done, failed = 0, []
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            name = info.get("longName") or info.get("shortName") or ""
            sector = normalise_sector(info.get("sector"))
            mc = info.get("marketCap")
        except Exception:
            failed.append(t)
            continue

        if not name:
            failed.append(t)
            continue

        store.set_universe_fields(
            conn, t, name=name, sector=sector,
            market_cap=float(mc) if mc else None,
            sub_tier=primary_tier(t))
        done += 1
        if verbose and done % 25 == 0:
            print(f"   enriched {done}/{len(tickers)}")
        time.sleep(pause)
    return done, failed


def main():
    ap = argparse.ArgumentParser(
        description="Add AI-chain tickers to the universe and backfill sector data.")
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--pause", type=float, default=0.4)
    ap.add_argument("--chain-only", action="store_true",
                    help="Skip enrichment of existing S&P names")
    args = ap.parse_args()

    conn = store.init_db(store.connect(args.db))
    today = date.today().isoformat()

    chain = ai_chain.all_tickers()
    existing = set(store.active_universe(conn))
    new = [t for t in chain if t not in existing]

    # Added with source='chain'. add_constituents keeps the original source on
    # a conflict, so an S&P member that also sits in the chain stays 'sp500'
    # and does not get retired by the next index refresh.
    if new:
        store.add_constituents(conn, [(t, "") for t in new], today, source="chain")
    print(f"chain tickers: {len(chain)} | already tracked: {len(chain) - len(new)} "
          f"| newly added: {len(new)}")

    # Every chain member needs a sub_tier, including ones already in the S&P.
    targets = chain if not args.chain_only else new
    print(f"enriching {len(targets)} ticker(s) from yfinance...")
    done, failed = enrich(conn, targets, args.pause)
    print(f"enriched {done}, failed {len(failed)}")
    if failed:
        print("   failed: " + ", ".join(failed))

    rows = store.universe_rows(conn)
    print(f"\nuniverse now {len(rows)} active")
    print(f"   with sector   : {sum(1 for r in rows if r['sector'])}")
    print(f"   with sub_tier : {sum(1 for r in rows if r['sub_tier'])}")
    print(f"   with mkt cap  : {sum(1 for r in rows if r['market_cap'])}")


if __name__ == "__main__":
    main()
