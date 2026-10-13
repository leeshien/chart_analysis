"""Pure rules deciding what a day's bar did to a level.

No I/O and no network, so every rule is testable against hand-written bars. The
whole alerting behaviour of the monitor is decided here.
"""


_EPS = 1e-9        # absorbs float error at an exact threshold


def role_of(level_price, prev_close):
    """Whether the level is acting as support or resistance right now.

    Re-derived every day from the previous close rather than read from the
    level's stored ``kind``, which was fixed against whatever the close was when
    the level was generated. If price cut through a support during the week that
    line now sits above price and behaves as resistance, and polarity flips then
    handle themselves.
    """
    return "support" if prev_close > level_price else "resistance"


def classify(bar, prev_close, level, tol_pct):
    """``"touch"``, ``"break"`` or None for one bar against one level.

    Break is evaluated first and wins: a day whose low enters the band but whose
    close ends underneath did not hold, and reporting it as a touch would read as
    the opposite of what happened.

    Break also requires a TRANSITION - the previous close must have been on the
    other side. Without that clause a stock trading below a level for months
    would report a fresh break every single evening.
    """
    tol = abs(level) * tol_pct
    low, high, close = bar["Low"], bar["High"], bar["Close"]

    if role_of(level, prev_close) == "support":
        if close < level - tol and prev_close >= level - tol:
            return "break"
        if low <= level + tol and close >= level - tol:
            return "touch"
        return None

    if close > level + tol and prev_close <= level + tol:
        return "break"
    if high >= level - tol and close <= level + tol:
        return "touch"
    return None


def shock(close, prev_close, pct):
    """``"jump"``, ``"drop"`` or None for a close-to-close move of ``pct``.

    Pure arithmetic on two closes, so this alert keeps working when Gemini is
    down, when levels are stale, and for a ticker that has never been scanned.

    It knows nothing about splits. Bars are stored auto-adjusted, so a 4:1 split
    reads as -75% here and would fire a confident, false DROP. The caller MUST
    skip any ticker whose history was re-downloaded this run for adjustment
    drift, and any ticker with fewer than two stored bars.
    """
    if not prev_close or close is None:
        return None
    change = close / prev_close - 1.0
    # A move of exactly pct must fire. For a clean 20%, close/prev - 1 evaluates
    # to 0.19999999999999996, so a bare >= silently drops the boundary case —
    # which is the value a threshold is most likely to be set on.
    if change >= pct - _EPS:
        return "jump"
    if change <= -pct + _EPS:
        return "drop"
    return None


def has_confluence(price, kind, other_levels, pct):
    """True if a level of the OTHER timeframe sits within ``pct`` on the same side.

    Where the weekly and daily charts independently place a level at the same
    price, that is the strongest signal the system produces, and it earns the
    interruptive alert tier.
    """
    for other in other_levels or []:
        if other.get("kind") != kind:
            continue
        if abs(other["price"] - price) / max(abs(price), 1e-9) <= pct:
            return True
    return False
