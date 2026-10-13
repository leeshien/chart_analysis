"""The price context the setup rules need beyond the two trend verdicts.

Each function answers one question about a daily bar frame as of bar ``pos``,
reading no bar after it, so a replay of an old day sees what that day saw.
"""
from datetime import timedelta

import numpy as np
import pandas as pd

import double_bottom_sr as dbs
import level_batch
import params
import setups
import timeframes

# The long-range high breakout: weekly highs over PEAK_WEEKS completed weeks,
# fired only on the first close above it in PEAK_QUIET_BARS daily bars, so a
# stock making new highs every week alerts once, not every Monday.
PEAK_WEEKS = setups.PEAK_WEEKS
PEAK_QUIET_BARS = params.get("setups.peak_quiet_bars")

# A double bottom's neckline and the resistance being hit are one price when
# they sit within NECKLINE_MATCH. The neckline is a bar's high and the level a
# band centre, so they rarely agree to the cent.
NECKLINE_MATCH = params.get("setups.neckline_match")

# A breakout retest: a close above the resistance inside the last RETEST_BARS.
RETEST_BARS = params.get("setups.retest_bars")


def higher_resistance_touched(df, pos, level_price, daily_levels, window, tol):
    """True if a daily level above ``level_price`` was reached in the window.

    ``window`` is the bars the daily trend verdict rests on, ending at ``pos``;
    today's bar is left out, because today is the alert itself. A level counts
    as higher only when the two touch bands do not overlap, so a near-duplicate
    of the alerted level is not mistaken for the top of the range.
    """
    start = max(0, pos - window + 1)
    highs = df["High"].to_numpy(dtype=float)[start:pos]
    if len(highs) == 0:
        return False
    reached = float(highs.max())
    floor = level_price * (1.0 + 2.0 * tol)
    return any(lv["price"] > floor and reached >= lv["price"] * (1.0 - tol)
               for lv in daily_levels)


def rally(df, pos, bars=setups.RALLY_BARS):
    """Close over the lowest low of the last ``bars`` bars, minus one."""
    low = float(df["Low"].to_numpy(dtype=float)[max(0, pos - bars + 1):pos + 1].min())
    return float(df["Close"].iloc[pos]) / low - 1.0 if low > 0 else 0.0


def closed_above_recently(df, pos, level_price, window, tol):
    """True if a close in the window before ``pos`` was above the level's band.

    The level is resistance today (the previous close is under it), so a close
    above it inside the window means price broke out and fell back.
    """
    start = max(0, pos - window + 1)
    closes = df["Close"].to_numpy(dtype=float)[start:pos]
    return bool(len(closes)) and float(closes.max()) > level_price * (1.0 + tol)


def double_bottom_neckline(df, pos, level_price):
    """True if a live double bottom's neckline is the resistance being hit.

    Runs the project's level-anchored detector (double_bottom_sr) over the
    daily bars up to ``pos``, with the same leg spacing the scans use. A base
    counts when its second low is inside the last leg_max_bars, it
    had not cleared its neckline before today, and the neckline is within
    NECKLINE_MATCH of the alerted level.
    """
    upto = df.iloc[:pos + 1]
    frame = upto.tail(timeframes.TIMEFRAMES["daily"]["trend_window"])
    knobs = timeframes.base_knobs("daily", level_batch.frame_for(upto, "weekly"))
    if len(frame) < knobs.leg_min_bars * 2:
        return False
    cfg = timeframes.config_for("daily", frame)
    closes = frame["Close"].to_numpy(dtype=float)
    last = len(frame) - 1
    for b in dbs.detect(frame, dbs.candidates_from_lows(frame, cfg, knobs), cfg,
                        knobs, "", "daily", "B"):
        if b.leg2_pos < last - knobs.leg_max_bars:
            continue
        if abs(b.neckline - level_price) / level_price > NECKLINE_MATCH:
            continue
        before_today = closes[b.leg2_pos + 1:last]
        if len(before_today) and float(before_today.max()) > b.neckline:
            continue
        return True
    return False


def reversal_pattern(df, pos, level_price, tol, window=RETEST_BARS):
    """The reversal base's sub-tag: double bottom, breakout retest, or None."""
    if double_bottom_neckline(df, pos, level_price):
        return setups.DOUBLE_BOTTOM
    if closed_above_recently(df, pos, level_price, window, tol):
        return setups.RETEST
    return None


def volume_on(df, pos, window=20):
    """``(volume, ratio)``: the bar's volume and its multiple of the median
    volume of the ``window`` bars before it (ratio None without history)."""
    vol = df["Volume"].to_numpy(dtype=float)
    prior = vol[max(0, pos - window):pos]
    med = float(np.median(prior)) if len(prior) else 0.0
    return float(vol[pos]), (float(vol[pos] / med) if med > 0 else None)


def format_volume(volume, ratio):
    """``vol 12.3M · 1.8x 20-day median``."""
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if volume >= div:
            text = f"vol {volume / div:.1f}{unit}"
            break
    else:
        text = f"vol {volume:.0f}"
    return text + (f" · {ratio:.1f}× 20-day median" if ratio else "")


def long_peak(df, pos, weeks=PEAK_WEEKS):
    """Highest high of the ``weeks`` completed weeks before ``pos``'s week.

    None when the history does not reach back that far: a stock listed more
    recently has no long-range high to break.
    """
    day = df.index[pos]
    week_start = pd.Timestamp((day - timedelta(days=day.weekday())).date())
    start = week_start - timedelta(weeks=weeks)
    if df.index[0] > start + timedelta(days=7):
        return None
    span = df[(df.index >= start) & (df.index < week_start)]
    return float(span["High"].max()) if len(span) else None


def high_breakout(df, pos, quiet=PEAK_QUIET_BARS):
    """The long-range peak if bar ``pos`` is a fresh close above it, else None.

    Fresh means no close in the previous ``quiet`` bars was already above the
    long-range high as it stood before them.
    """
    peak = long_peak(df, pos)
    if peak is None or float(df["Close"].iloc[pos]) <= peak:
        return None
    if pos >= quiet:
        ref = long_peak(df, pos - quiet)
        recent = float(df["Close"].iloc[pos - quiet:pos].max())
        if ref is not None and recent > ref:
            return None
    return peak
