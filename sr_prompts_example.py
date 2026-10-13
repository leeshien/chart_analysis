"""A generic support/resistance prompt: ILLUSTRATIVE ONLY.

The monitor's tuned prompt lives in ``sr_prompts.py``, which is kept private.
level_batch.py uses it when present and falls back to this module otherwise.
To use your own, copy this file to ``sr_prompts.py`` and edit the wording;
keep ``RESPONSE_SCHEMA`` and the final-line JSON instruction, which the level
parser (sr_parse.py) depends on.
"""
import json

# One entry per horizontal level; sr_parse reads these keys.
RESPONSE_SCHEMA = [
    {"price": 0.0, "kind": "support", "confidence": 0.0, "major": False, "reasoning": ""},
]


def chart_bounds(df) -> dict:
    """The numeric facts the model would otherwise have to read off the axes."""
    return {
        "low": float(df["Low"].min()),
        "high": float(df["High"].max()),
        "last_close": float(df["Close"].iloc[-1]),
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
    }


def build_sr_prompt(df, cfg) -> str:
    """Ask for horizontal support and resistance levels as a JSON array."""
    b = chart_bounds(df)
    return (
        "Identify the horizontal support and resistance levels on this stock "
        "chart.\n\n"
        f"The chart runs from {b['start']} to {b['end']}; prices range from "
        f"{b['low']:.2f} to {b['high']:.2f} and the last close is "
        f"{b['last_close']:.2f}.\n\n"
        f"Give at most {cfg.gemini_sr_max_levels} levels. For each: price, kind "
        "('support' below the last close, 'resistance' above), confidence from "
        "0.0 to 1.0, major (true for the most important levels) and a short "
        "reasoning.\n\n"
        "On the final line, output ONLY a JSON array in this shape:\n"
        f"{json.dumps(RESPONSE_SCHEMA)}"
    )
