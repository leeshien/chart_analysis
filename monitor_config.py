from dataclasses import dataclass
from pathlib import Path

import params


@dataclass(frozen=True)
class MonitorConfig:
    """Phase 1 settings. Kept out of config.py so the tuned S/R values are untouched."""

    db_path: Path = Path("state/monitor.db")

    # yfinance throttling. Yahoo's tolerance changes; these are starting values.
    fetch_batch_size: int = 20          # tickers per yf.download call
    fetch_pause_seconds: float = 2.0    # pause between batches
    fetch_max_retries: int = 3
    first_run_period: str = "max"       # one-time full history pull

    # Retroactive-adjustment detection.
    fetch_overlap_days: int = 10        # calendar days of overlap re-fetched each run
    drift_rel_tolerance: float = 0.001  # 0.1%: above rounding noise, below a dividend step

    sp500_url: str = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    # A parse returning far fewer than ~500 names means the page structure moved,
    # not that the index shrank. Refuse the refresh rather than record a success.
    min_constituents: int = 400

    # alerting - tuned values come from params (see params.py).
    # Shock threshold scales with the ticker: a 20% day is a multi-year event
    # for DUK (1.48% median bar) and an earnings-day occurrence for AAOI
    # (9.45%), so it is a multiple of the median bar, clamped to a floor (the
    # quietest utilities) and a cap (so the wildest names stay reachable).
    shock_range_mult: float = params.get("monitor.shock_range_mult")
    shock_min_pct: float = params.get("monitor.shock_min_pct")
    shock_max_pct: float = params.get("monitor.shock_max_pct")
    # fallback when a bar range cannot be measured
    shock_pct: float = params.get("monitor.shock_pct")
    # weekly and daily levels this close count as one (confluence)
    confluence_pct: float = params.get("monitor.confluence_pct")
    # trading days clear of the band before a touch alerts again
    visit_gap_days: int = params.get("monitor.visit_gap_days")
    digest_limit: int = 20           # lines in the evening message
    max_urgent_push: int = 5         # individual push messages before folding into the digest


MONITOR = MonitorConfig()
