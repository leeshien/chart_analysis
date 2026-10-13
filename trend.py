"""Is this stock trending up, trending down, or going sideways?

Two numbers, because direction alone cannot answer the question. A stock can
end a year far above where it started and still be a range that happened to
finish high; a stock can drift relentlessly on a shallow slope and be a genuine
downtrend. So:

- **drift** - the move the trend accounts for, divided by the noise the stock
  generates over the same stretch: ``slope * sqrt(window) / median bar range``,
  on LOG price so a compounding rate is measured rather than an absolute one.
  Dividing by the bar range makes it comparable across tickers, as shock_pct
  and the touch tolerance do. The sqrt makes it comparable across WINDOW
  LENGTHS, which a per-bar slope is not: the same total move spread over more
  bars yields a smaller per-bar number, so a threshold tuned on 52 bars called
  99% of stocks sideways when the window grew to 1250. Trend accumulates with
  the window while noise accumulates with its square root, so their ratio is
  the thing that means one fixed number.
- **fit** - the R-squared of that line, which is what separates trending from
  sideways honestly. Low R-squared means the drift is an accident of the
  endpoints, whatever its sign.

Deliberately not a moving-average cross: those answer "has price been above a
number", which whipsaws through exactly the ranges this needs to call sideways.
"""
from dataclasses import dataclass

import numpy as np

import params

# DEFAULT_WINDOW is the fallback for a caller with no timeframe. Prefer
# window_for(), because 52 is a WEEKLY bar count: used on daily bars it means
# two and a half months, which is the class of error timeframes.py exists to
# prevent.
DEFAULT_WINDOW = 52          # bars looked back (one year of weekly bars)

# The thresholds are tuned values, loaded from params (see params.py):
#
# - MIN_FIT: below this R-squared the drift is noise and the verdict is
#   "undecided".
# - FLAT_DRIFT: inside +-this the stock is sideways. Note what drift is and is
#   not. It compares the move to the distance a random walk of the same bar
#   size would travel - NOT the regression t-statistic, which scales with the
#   window. So the threshold asks "did price travel somewhere", not "is the
#   slope significant".
# - Weekly has its own band: overnight gaps fall outside a daily High-Low but
#   inside a weekly one, so weekly drift reads a little lower than daily on the
#   same stock and calendar window.
MIN_FIT = params.get("trend.min_fit")
FLAT_DRIFT = params.get("trend.flat_drift_daily")
_FLAT_DRIFT_BY_TIMEFRAME = {"daily": FLAT_DRIFT,
                            "weekly": params.get("trend.flat_drift_weekly")}


# Bars per month, used to widen the window in month-sized steps. Both are the
# SAME calendar step, which is the point: a month means a month whether the
# bars are weekly or daily.
_BARS_PER_MONTH = {"weekly": 52 / 12.0, "daily": 252 / 12.0}
_MONTHS = tuple(params.get("trend.months"))


@dataclass(frozen=True)
class Trend:
    state: str               # "up" | "down" | "sideways" | "undecided"
    drift: float             # signal-to-noise of the slope
    fit: float               # R-squared of the line
    bars: int                # how many bars the verdict rests on
    months: float = 0.0      # the window that produced it, in months

    @property
    def trending(self):
        return self.state in ("up", "down")

    @property
    def decided(self):
        """A straight line described the data well enough to mean something.

        Sideways IS a decision - the stock did not go anywhere, which the
        drift band establishes on its own. Undecided means price DID move but
        no line describes how, so no verdict about direction has been earned.
        """
        return self.state != "undecided"


def window_for(timeframe):
    """Bars of history the trend is measured over, per timeframe.

    The same window the scans search, so a trend verdict describes the history
    the levels and the previous peak came from rather than a different slice.
    """
    import timeframes
    return timeframes.TIMEFRAMES[timeframe]["trend_window"]


def _median_bar_range(df):
    rng = (df["High"] - df["Low"]) / df["Close"]
    v = rng.to_numpy(dtype=float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if len(v) else 0.0


def fit_line(closes):
    """``(slope_per_bar, r_squared)`` of a least-squares line through log price.

    Log price so the slope is a compounding rate: a stock going 10 -> 20 and one
    going 100 -> 200 have the same trend, which a linear fit would deny.
    """
    y = np.log(np.asarray(closes, dtype=float))
    n = len(y)
    if n < 3 or not np.all(np.isfinite(y)):
        return 0.0, 0.0
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(max(0.0, r2))


def classify(df, pos=None, window=DEFAULT_WINDOW, min_fit=MIN_FIT,
             flat_drift=FLAT_DRIFT, bar_range=None):
    """Trend state at ``pos`` (default: the last bar), from the bars before it.

    Only data up to and including ``pos`` is used, so this is safe to apply at a
    historical bar without leaking the future - unlike sr_major.reaction, which
    looks forward and must never be used this way.
    """
    if pos is None:
        pos = len(df) - 1
    start = max(0, pos - window + 1)
    seg = df.iloc[start:pos + 1]
    n = len(seg)
    if n < 10:
        return Trend("undecided", 0.0, 0.0, n)

    slope, r2 = fit_line(seg["Close"].to_numpy(dtype=float))
    rng = bar_range if bar_range is not None else _median_bar_range(seg)
    # sqrt(n), not n: the trend's contribution grows with the window while the
    # noise grows with its square root, so this ratio holds its meaning whether
    # the window is 52 bars or 1250.
    drift = slope * np.sqrt(n) / rng if rng > 0 else 0.0

    # Drift is tested FIRST. A stock whose drift is inside the flat band did
    # not go anywhere, and that is sideways whatever the fit says: a low
    # R-squared with a near-zero slope just means price wobbled and ended where
    # it started, which is exactly what a range looks like.
    #
    # Undecided is reserved for the opposite corner - price moved a long way
    # but no line describes how, so it travelled in a jump or a reversal rather
    # than a trend. That is the case a wider window can genuinely resolve.
    if abs(drift) < flat_drift:
        state = "sideways"
    elif r2 < min_fit:
        state = "undecided"
    else:
        state = "up" if drift > 0 else "down"
    return Trend(state, float(drift), float(r2), n)


def flat_drift_for(timeframe):
    """The sideways band for this timeframe's bars."""
    return _FLAT_DRIFT_BY_TIMEFRAME[timeframe]


def months_to_bars(timeframe, months):
    """Calendar months expressed in this timeframe's bars."""
    return max(10, int(round(_BARS_PER_MONTH[timeframe] * months)))


def classify_adaptive(df, timeframe, pos=None, months=_MONTHS,
                      min_fit=MIN_FIT, flat_drift=None):
    """Start at the shortest window in ``months`` and widen until it decides.

    A short window answers the question people actually ask of a chart - what
    is this stock doing now - so it is tried first. Widening only happens when
    a straight line does not describe the data well enough to mean anything;
    once a verdict is earned, including "sideways", the search stops.

    Returns the first decided Trend. If the widest window still will not fit
    a line, the undecided verdict from that window comes back, and that is a
    statement about this method rather than about the stock: a linear trend is
    not the right description of that price path.
    """
    if flat_drift is None:
        flat_drift = flat_drift_for(timeframe)
    last = None
    for m in months:
        w = months_to_bars(timeframe, m)
        v = classify(df, pos=pos, window=w, min_fit=min_fit,
                     flat_drift=flat_drift)
        v = Trend(v.state, v.drift, v.fit, v.bars, float(m))
        if v.decided:
            return v
        last = v
    return last
