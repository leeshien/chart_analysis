"""Colours, type sizes and glyphs shared by the report's summary and chart pages.

One place, so the summary chip for a setup and the same setup on its chart page
can never drift apart. The four setup colours are categorical slots validated as
a set for colour-vision deficiency (worst all-pairs CVD delta-E 6.1, magenta
against aqua, legal only because every use carries a text label). Aqua,
magenta and yellow sit below 3:1 on the page, so a setup colour is never the only cue:
every chip and row carries the setup's name beside it.
"""
import setups

PAGE = (11.69, 8.27)            # A4 landscape, inches

SURFACE = "#fcfcfb"
SURFACE_2 = "#f3f2ee"           # tiles, header bands
RULE = "#dcdad3"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8882"
WARN = "#d03b3b"                # staleness warnings, always with a label

SUPPORT = "#2a78d6"
RESISTANCE = "#eb6834"
ALERT = "#4a3aa7"
CANDLE_UP = "#4fb28a"
CANDLE_DOWN = "#e06666"
VOLUME = "#d9d7d0"

SETUP_COLOR = {
    setups.CONFIRMED: "#e87ba4",
    setups.BREAKOUT: "#4a3aa7",
    setups.BASING: "#2a78d6",
    setups.PULLBACK: "#1baf7a",
    setups.MAJOR_SUPPORT: "#c2410c",
}

OUTLOOK_GLYPH = {setups.BULLISH: "▲", setups.SIDEWAYS: "↔",
                 setups.BEARISH: "▼"}

TREND_GLYPH = {"up": "↑", "down": "↓", "sideways": "↔",
               "undecided": "?"}
TREND_WORD = {"up": "Uptrend", "down": "Downtrend", "sideways": "Sideways",
              "undecided": "Undecided"}


def tint(hex_colour, amount=0.85):
    """The colour mixed toward white: a chip background that keeps its hue."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = [round(c + (255 - c) * amount) for c in (r, g, b)]
    return "#" + "".join(f"{c:02x}" for c in mix)


def trend_pair(row):
    """``D ↔  W ↑`` for a row carrying daily_trend and weekly_trend."""
    d = TREND_GLYPH.get(row.get("daily_trend"), "–")
    w = TREND_GLYPH.get(row.get("weekly_trend"), "–")
    return f"D {d}   W {w}"


def level_text(row):
    """``break · R 137.14 (D)``, with ``major`` and a confluence star."""
    if row.get("price") is None:
        pct = (row.get("score") or 0) * 100
        return f"{row['event']} {pct:+.1f}%"
    if setups.is_long_high(row):
        return (f"{row['event']} · {setups.LONG_HIGH_SHORT} "
                f"{row['price']:,.2f} (W)")
    if row.get("setup") == setups.CONFIRMED:
        tf = {"weekly": "W", "daily": "D"}.get(row.get("timeframe"), "")
        return f"closed through R {row['price']:,.2f} ({tf})"
    side = "S" if row.get("kind") == "support" else "R"
    tf = {"weekly": "W", "daily": "D"}.get(row.get("timeframe"), "")
    major = "major " if row.get("is_major") else ""
    star = "  ★" if row.get("confluence") else ""
    return f"{row['event']} · {major}{side} {row['price']:,.2f} ({tf}){star}"
