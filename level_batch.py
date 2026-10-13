"""Build, submit and collect Gemini batch jobs for S/R level detection.

All Gemini contact for Phase 2 lives here. Level refresh has no latency
requirement, so it uses the Batch API: half price, and no per-minute rate limit
to design around.

Each request is tagged with its ticker in ``metadata``, which the API returns on
the matching ``InlinedResponse``. Responses are therefore matched by tag, never
by list position — a batch that returns out of order or drops a failed entry
would otherwise silently attribute one ticker's levels to another.
"""
from pathlib import Path

from google import genai
from google.genai import types

import bars as bars_mod
import chart_image
try:
    # The tuned prompt is private (git-ignored); see sr_prompts_example.py.
    import sr_prompts
except ModuleNotFoundError:
    import sys as _sys

    import sr_prompts_example as sr_prompts
    print("[level_batch] sr_prompts.py not found; using the generic "
          "sr_prompts_example.py prompt", file=_sys.stderr)
import timeframes

_MIN_BARS = 30          # below this a chart is not worth an API call

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def frame_for(daily, timeframe):
    """The bar frame this timeframe analyses, trimmed to its lookback."""
    tf = timeframes.TIMEFRAMES[timeframe]
    df = daily if timeframe == "daily" else bars_mod.derive_weekly(daily)
    return df.tail(tf["lookback_bars"])


def build_request(ticker, daily, timeframe, image_dir):
    """One InlinedRequest, or None if the ticker has too little history."""
    df = frame_for(daily, timeframe)
    if len(df) < _MIN_BARS:
        return None

    cfg = timeframes.config_for(timeframe)
    png = Path(image_dir) / f"{ticker}_{timeframe}.png"
    chart_image.render_line(df, png, max_width_px=cfg.gemini_max_image_width)

    prompt = sr_prompts.build_sr_prompt(df, cfg)
    return types.InlinedRequest(
        model=cfg.gemini_model,
        contents=[types.Content(role="user", parts=[
            types.Part.from_bytes(data=png.read_bytes(), mime_type="image/png"),
            types.Part.from_text(text=prompt),
        ])],
        metadata={"ticker": ticker, "timeframe": timeframe},
        config=types.GenerateContentConfig(
            temperature=0.0, max_output_tokens=cfg.gemini_max_tokens),
    )


def submit(requests, model, display_name):
    """Create the batch job. Returns its name, which the collect phase needs."""
    job = _get_client().batches.create(
        model=model, src=requests,
        config=types.CreateBatchJobConfig(display_name=display_name))
    return job.name


def poll(job_name):
    """Current BatchJob. State is compared against types.JobState values."""
    return _get_client().batches.get(name=job_name)


def responses_by_ticker(inlined_responses) -> dict:
    """{ticker: raw text} from a finished job, matched by metadata tag.

    Entries carrying an error are dropped rather than recorded as empty text: an
    empty parse would replace good levels with nothing, and a per-request failure
    should leave the previous refresh in place.
    """
    out = {}
    for item in inlined_responses or []:
        meta = getattr(item, "metadata", None) or {}
        ticker = meta.get("ticker")
        if not ticker or getattr(item, "error", None):
            continue
        response = getattr(item, "response", None)
        text = getattr(response, "text", None) if response else None
        if text:
            out[ticker] = text
    return out
