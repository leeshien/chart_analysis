"""Which alerts are worth a human's attention, and why.

A level being touched is not by itself a reason to look at a chart: on a normal
evening 100-170 levels are touched or broken across the universe. These rules
keep only the situations this project trades on, each defined by the daily and
weekly trend verdicts from trend.py and by what price did at a level. The
thresholds are tuned values loaded from params (see params.py).

- **confirmed_breakout** ("Breakout") - the buy-in chance, either of:
  - price CLOSES through the resistance of a Breakout setup the same stock
    showed in the last CONFIRM_BARS bars. The setup is the base pressing on
    its ceiling; this is the ceiling giving way. Decided with the alert
    history, so it lives in check_alerts.confirmed_breakouts, not here.
  - price closes above its highest weekly high of the last PEAK_WEEKS
    completed weeks, in any trend (pattern ``long_high``;
    setup_context.high_breakout).
- **breakout** (A) - daily sideways, weekly up, and price touches or breaks
  the TOP of the daily range: no higher daily resistance was touched inside the
  window the daily verdict was measured over. A base forming inside a rising
  longer trend and pressing on its ceiling: a potential double bottom or
  breakout, the best entry signal this project looks for. Monitor immediately.
- **basing** (B, shown as "Reversal base") - daily down, weekly sideways, price
  touches or breaks resistance, AND price has rallied at least RALLY_MIN off
  its lowest low of the last RALLY_BARS bars. The decline has stalled inside a
  longer range and bounced hard into the first ceiling above it. A sub-tag
  says which shape it is, when that can be told (``PATTERN_LABEL``).
- **pullback** (C, bullish) - weekly up, and either price touches a MAJOR
  support while the daily trend is up or sideways, or (daily sideways) price
  is at a resistance BELOW a higher daily resistance it already touched inside
  the daily window: it is in the middle of the range, on its way back down
  from the top, not at the ceiling.
- **major_support** (C, bearish) - every other hit of a MAJOR support: a break,
  or a touch while the weekly trend is not up or the daily trend is falling.
  Worth watching to see whether the decline continues or turns.

Pure functions, no I/O, so every rule is testable against hand-written inputs.
"""
import params

CONFIRMED = "confirmed_breakout"
BREAKOUT = "breakout"
BASING = "basing"
PULLBACK = "pullback"
MAJOR_SUPPORT = "major_support"

# A Breakout setup stays live this many daily bars: a close through its
# resistance inside that span is a confirmed breakout.
CONFIRM_BARS = params.get("setups.confirm_bars")

# Reversal base needs a bounce, not a drift: the close at least RALLY_MIN above
# the lowest low of the last RALLY_BARS daily bars.
RALLY_MIN = params.get("setups.rally_min")
RALLY_BARS = params.get("setups.rally_bars")

# The long-range high a Breakout can also clear, named for its span.
PEAK_WEEKS = params.get("setups.peak_weeks")
_YEARS = PEAK_WEEKS / 52
LONG_HIGH_NAME = (f"{_YEARS:.0f}-year high" if PEAK_WEEKS % 52 == 0
                  else f"{PEAK_WEEKS}-week high")
LONG_HIGH_SHORT = (f"{_YEARS:.0f}-yr high" if PEAK_WEEKS % 52 == 0
                   else f"{PEAK_WEEKS}-wk high")

# Display order: the setup to act on first leads everywhere it is listed.
ORDER = (CONFIRMED, BREAKOUT, BASING, PULLBACK, MAJOR_SUPPORT)

LABEL = {
    CONFIRMED: "Breakout",
    BREAKOUT: "Breakout setup",
    BASING: "Reversal base",
    PULLBACK: "Pullback to support",
    MAJOR_SUPPORT: "Major support test",
}

# One line each, for the summary tiles.
DEFINITION = {
    CONFIRMED: f"Closes through setup R or {LONG_HIGH_SHORT}",
    BREAKOUT: "D sideways, W up, top of range",
    BASING: f"D down, W flat, +{RALLY_MIN:.0%} in {RALLY_BARS} bars",
    PULLBACK: "W up, back at range support",
    MAJOR_SUPPORT: "Major S broken or tested weak",
}

# Pushed one by one and listed under MONITOR NOW.
URGENT = (CONFIRMED, BREAKOUT)

# The two outlook groups. Bullish holds the setups that point to a move up;
# bearish holds major support broken or under pressure, where the open question
# is whether the decline continues.
BULLISH = "Bullish"
BEARISH = "Bearish"
# Only for a stock whose same-day setups disagree while the weekly trend is
# sideways: a range, with both levels in play (check_alerts.resolve_outlooks).
SIDEWAYS = "Sideways"
OUTLOOK = {CONFIRMED: BULLISH, BREAKOUT: BULLISH, BASING: BULLISH,
           PULLBACK: BULLISH, MAJOR_SUPPORT: BEARISH}

# A reversal base's shape, when it can be told on the day it fires.
#
# - double_bottom: two lows at one support with a peak between, and the
#   resistance being hit IS that peak (the neckline).
# - breakout_retest: price closed above this resistance recently and fell back
#   under it. Whether that was a pause or a trap cannot be known on the day;
#   it shows afterwards, by a close back above or a break of the retest low.
#
# Neither found leaves the plain "Reversal base".
DOUBLE_BOTTOM = "double_bottom"
RETEST = "breakout_retest"
# A Breakout through the long-range weekly high rather than a setup's
# resistance.
LONG_HIGH = "long_high"
PATTERN_LABEL = {DOUBLE_BOTTOM: "double bottom", RETEST: "breakout retest",
                 LONG_HIGH: LONG_HIGH_NAME}

_LEVEL_EVENTS = ("touch", "break")


def classify(event, role, is_major, daily_trend, weekly_trend,
             higher_r_touched=False, rally=None):
    """The setup this level event belongs to, or None.

    ``role`` is the level's role as of the previous close ("support" or
    "resistance"), so a resistance that price just broke through still reads
    as resistance here, which is the breakout this is looking for.

    ``higher_r_touched``: a daily resistance above this level was touched
    inside the daily trend window. ``rally``: close over the lowest low of the
    last RALLY_BARS bars, minus one; None when it was not measured.
    """
    if event not in _LEVEL_EVENTS:
        return None
    if role == "resistance":
        if daily_trend == "sideways" and weekly_trend == "up":
            return PULLBACK if higher_r_touched else BREAKOUT
        if daily_trend == "down" and weekly_trend == "sideways":
            if rally is not None and rally >= RALLY_MIN:
                return BASING
        return None
    if role == "support" and is_major:
        if (event == "touch" and weekly_trend == "up"
                and daily_trend in ("up", "sideways")):
            return PULLBACK
        return MAJOR_SUPPORT
    return None


def display(row):
    """The setup's name with its pattern, e.g. ``Reversal base · double bottom``."""
    label = LABEL.get(row.get("setup"), "")
    pattern = PATTERN_LABEL.get(row.get("pattern"))
    return f"{label} · {pattern}" if label and pattern else label


# The same, short enough for a summary row.
_SHORT = {BASING: "Rev. base", PULLBACK: "Pullback", MAJOR_SUPPORT: "Support test",
          BREAKOUT: "BO setup",
          CONFIRMED: "Breakout"}
_SHORT_PATTERN = {DOUBLE_BOTTOM: "dbl bottom", RETEST: "retest",
                  LONG_HIGH: LONG_HIGH_SHORT}


def short_display(row):
    """``Rev. base · dbl bottom``: the setup and its pattern for a table row."""
    label = _SHORT.get(row.get("setup"), "")
    pattern = _SHORT_PATTERN.get(row.get("pattern"))
    return f"{label} · {pattern}" if label and pattern else label


def is_long_high(row):
    """A Breakout through the long-range weekly high."""
    return row.get("pattern") == LONG_HIGH


def rank(setup):
    """Sort key: breakout first, unknown last."""
    return ORDER.index(setup) if setup in ORDER else len(ORDER)
