"""Per-timeframe settings, as overrides on the tuned chart-analysis config.

config.py holds values tuned by eye against weekly charts and must not change.
Every field there named *_weeks is really a BAR COUNT: the scoring functions take
whatever frame they are handed and count rows. Feeding daily bars without scaling
would make sr_episode_gap_weeks mean four days and sr_reaction_window thirteen
days, producing episode counts and reaction measurements that look plausible and
mean something entirely different.

The weekly settings deliberately match the validated defaults. Only the daily
timeframe departs from them, and each departure is justified below.
"""
from dataclasses import dataclass, replace

import numpy as np

import params
from config import DEFAULT

_DAILY_BARS_PER_WEEK = 5

# Tuned per-timeframe values come from params (see params.py):
#
# - touch_tol: flat touch band, the fallback when no frame is supplied.
# - tol_range_mult: the touch band as a multiple of the ticker's own median
#   bar range. A fixed percentage cannot mean the same thing for a utility and
#   a semiconductor; scaling to the name's bar size does.
# - major_min_episodes / major_min_score: the floors a level must clear to be
#   major.
# - leg_min_bars / leg_max_bars: double-bottom geometry, bottom to bottom. The
#   daily pair must describe the SAME calendar window as the weekly pair, in
#   daily bars: reusing the weekly bar counts would accept a base a few weeks
#   wide, which is not a double bottom at all.


def _tuned(tf):
    return {
        "touch_tol": params.get(f"touch.tol_fallback_{tf}"),
        "tol_range_mult": params.get(f"touch.tol_range_mult_{tf}"),
        "major_min_episodes": params.get(f"levels.major_min_episodes_{tf}"),
        "major_min_score": params.get(f"levels.major_min_score_{tf}"),
        "leg_min_bars": params.get(f"double_bottom.leg_min_bars_{tf}"),
        "leg_max_bars": params.get(f"double_bottom.leg_max_bars_{tf}"),
    }


TIMEFRAMES = {
    "weekly": {
        "lookback_bars": 261,          # ~5 years of weekly bars
        "image_width": 1000,           # 851x467 rendered: ~3.2 px per bar
        "episode_gap_bars": 4,
        "reaction_window_bars": 13,
        # 20% was 3.6x a median weekly bar across the index, so this preserves
        # today's average while scaling with the name.
        "reaction_full_range_mult": 3.6,
        "side_aware": True,
        "refresh_days": 30,
        # Trend is measured over the same bars the scans search, so the
        # verdict describes the history the levels and the prior peak
        # were derived from rather than some other window.
        "trend_window": 261,
        "swing_distance_bars": 4,
        **_tuned("weekly"),
    },
    "daily": {
        "lookback_bars": 630,          # ~2.5 years of daily bars
        "image_width": 1600,           # ~1316x724: ~2.1 px per bar, still 2 tiles
        "episode_gap_bars": 4 * _DAILY_BARS_PER_WEEK,
        "reaction_window_bars": 13 * _DAILY_BARS_PER_WEEK,
        # A flat 20% meant 9.5x a median daily bar but only 3.6x a weekly one —
        # the same constant measuring two different things. Scaling it to the
        # ticker's own bar size stops the term doubling as a volatility proxy,
        # which is what left low-beta names unable to earn it at all.
        "reaction_full_range_mult": 9.5,
        # Over 630 daily bars the side-agnostic mask rewards mid-range prices,
        # which is how MSFT's primary support landed in the middle of a
        # nine-month range rather than on an edge price ever turned at.
        "side_aware": True,
        "refresh_days": 7,
        # Matches the double-bottom and breakout scans, which search 1250
        # daily bars rather than the 630 that size a legible image.
        "trend_window": 1250,
        "swing_distance_bars": 4 * _DAILY_BARS_PER_WEEK,
        **_tuned("daily"),
    },
}


def median_bar_range(df) -> float:
    """Median (High - Low) / Close: how big a typical bar is, as a fraction."""
    if df is None or len(df) == 0:
        return 0.0
    rng = (df["High"] - df["Low"]) / df["Close"]
    return float(np.median(rng.to_numpy(dtype=float)))


def config_for(timeframe, df=None):
    """A Config carrying this timeframe's settings. DEFAULT is never mutated.

    Pass ``df`` to scale the touch tolerance to that ticker's own volatility
    where the timeframe asks for it. Without a frame the fixed fallback is used,
    so every existing caller still works.
    """
    tf = TIMEFRAMES[timeframe]

    measured = median_bar_range(df) if df is not None else 0.0

    tol = tf["touch_tol"]
    mult = tf.get("tol_range_mult")
    if mult and measured > 0:
        tol = mult * measured

    reaction_full = DEFAULT.sr_reaction_full
    rmult = tf.get("reaction_full_range_mult")
    if rmult and measured > 0:
        reaction_full = rmult * measured

    return replace(
        DEFAULT,
        sr_reaction_full=reaction_full,
        sr_touch_tol_pct=tol,
        sr_episode_gap_weeks=tf["episode_gap_bars"],
        sr_reaction_window=tf["reaction_window_bars"],
        gemini_max_image_width=tf["image_width"],
        sr_side_aware_touches=tf["side_aware"],
        sr_major_min_episodes=tf["major_min_episodes"],
        sr_major_min_score=tf["major_min_score"],
        swing_distance_weeks=tf["swing_distance_bars"],
    )


# Double-bottom geometry shared by both timeframes. The rise floor is a
# multiplier on the median WEEKLY bar range; the retracement band is
# dimensionless and therefore needs no calibration at all.
_NECKLINE_RISE_MULT = 2.5
_NECKLINE_RISE_FALLBACK = 0.10   # used only when no weekly bars are available
_RETRACE_LO = 0.20
_RETRACE_HI = 0.75


@dataclass(frozen=True)
class BaseKnobs:
    """Geometry limits for one timeframe's double-bottom search."""
    leg_min_bars: int
    leg_max_bars: int
    rise_floor: float
    retrace_lo: float
    retrace_hi: float


def base_knobs(timeframe, weekly_df):
    """Double-bottom geometry for ``timeframe``.

    ``weekly_df`` is ALWAYS the weekly frame, whichever timeframe is being
    scanned. The leg ranges describe the same calendar window in both, so a
    floor derived from the local bar size would demand 13.8% of a weekly pass
    and 5.5% of a daily one for the identical structure — the exact class of
    error this module exists to prevent.
    """
    tf = TIMEFRAMES[timeframe]
    rng = median_bar_range(weekly_df)
    floor = _NECKLINE_RISE_MULT * rng if rng > 0 else _NECKLINE_RISE_FALLBACK
    return BaseKnobs(
        leg_min_bars=tf["leg_min_bars"],
        leg_max_bars=tf["leg_max_bars"],
        rise_floor=float(floor),
        retrace_lo=_RETRACE_LO,
        retrace_hi=_RETRACE_HI,
    )
