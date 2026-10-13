import json

import numpy as np

from levels import Level, _price_histogram, _zone_peaks, count_touches


def _truthy(val) -> bool:
    """Tolerant bool: models return true, "true", "yes" or 1 interchangeably."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() in ("true", "yes", "y", "1")


def _extract_array(text):
    """Pull the last JSON array out of the model's reply.

    The prompt asks for prose first and JSON on the final line, so scanning from
    the last '[' backwards finds the verdict rather than any array quoted earlier
    in the reasoning.
    """
    if not text:
        return None
    end = text.rfind("]")
    if end == -1:
        return None
    # Try successively earlier '[' so a nested array in the prose cannot win.
    start = text.rfind("[", 0, end)
    while start != -1:
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            start = text.rfind("[", 0, start)
            continue
        return data if isinstance(data, list) else None
    return None


def parse_sr_response(text, df, cfg) -> list:
    """Normalize the model's reply into a list of clean level dicts.

    Malformed output yields an empty list rather than raising. Prices outside the
    chart's own range (widened by sr_price_range_margin) are dropped as
    hallucinations — the model cannot have seen a level that is off the chart.
    """
    data = _extract_array(text)
    if not data:
        return []

    lo = float(df["Low"].min())
    hi = float(df["High"].max())
    margin = cfg.sr_price_range_margin
    floor, ceil = lo * (1.0 - margin), hi * (1.0 + margin)

    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            price = float(item.get("price"))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(price) or not (floor <= price <= ceil):
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        kind = str(item.get("kind", "")).strip().lower()
        out.append({
            "price": price,
            "gemini_kind": kind if kind in ("support", "resistance") else "",
            "confidence": max(0.0, min(1.0, conf)),
            "major": _truthy(item.get("major")),
            "reasoning": str(item.get("reasoning", "")),
        })
        if len(out) >= cfg.gemini_sr_max_levels:
            break
    return out


def candidate_prices(df, pivots, cfg) -> list:
    """Prices a model level may snap to: zigzag pivots plus histogram zone centres.

    These are the places the data itself says are significant, so snapping to one
    puts the drawn line on a real bar instead of floating between candles.
    """
    prices = [float(p.price) for p in pivots]
    edges, weights = _price_histogram(df, cfg)
    for a, b in _zone_peaks(weights, cfg):
        prices.append((float(edges[a]) + float(edges[b + 1])) / 2.0)
    return prices


def snap_price(price, candidates, cfg):
    """Snap ``price`` to the nearest candidate within sr_snap_max_pct.

    Returns ``(price, snapped)``. Snapping is deliberately bounded: forcing a
    distant price onto a candidate would rewrite a genuine disagreement between
    the two methods into false agreement, and the comparison is judged by eye.
    """
    if not candidates or price is None or not np.isfinite(price) or price <= 0:
        return price, False
    nearest = min(candidates, key=lambda c: abs(c - price))
    if abs(nearest - price) / abs(price) <= cfg.sr_snap_max_pct:
        return float(nearest), True
    return price, False


def build_levels(raw, df, pivots, cfg):
    """Turn parsed level dicts into Level objects on the same footing as find_levels.

    ``kind`` is re-derived from the last close for both methods so the two panels
    share one convention; the model's own label travels separately in ``extras``.
    Returns ``(levels, extras)`` where extras[i] carries gemini_kind, snapped and
    reasoning for level i.
    """
    if not raw:
        return [], []

    candidates = candidate_prices(df, pivots, cfg)
    last_close = float(df["Close"].iloc[-1])
    tol_pct = cfg.sr_touch_tol_pct
    dates_all = list(df.index.date)
    lows = df["Low"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)

    levels, extras = [], []
    for item in raw:
        price, snapped = snap_price(item["price"], candidates, cfg)
        tol = abs(price) * tol_pct
        hit = (lows <= price + tol) & (highs >= price - tol)
        touched_dates = [d for d, keep in zip(dates_all, hit) if keep]
        first_date = touched_dates[0] if touched_dates else dates_all[0]
        last_date = touched_dates[-1] if touched_dates else dates_all[-1]

        levels.append(Level(
            price=price,
            kind="support" if price < last_close else "resistance",
            strength=item["confidence"],
            touches=count_touches(df, price, cfg),
            zone_low=price - tol,
            zone_high=price + tol,
            first_date=first_date,
            last_date=last_date,
        ))
        extras.append({
            "gemini_kind": item["gemini_kind"],
            "snapped": snapped,
            "major": item.get("major", False),
            "reasoning": item["reasoning"],
        })
    return levels, extras


def model_major_levels(levels, extras) -> dict:
    """Levels the MODEL flagged major, grouped by kind.

    Kept unfiltered: a chart can legitimately have several major supports, so a
    model flagging three is probably right and demoting two would discard
    information. The computed verdict in sr_major is scored separately.
    """
    out = {}
    for i, lv in enumerate(levels):
        if i < len(extras) and extras[i].get("major"):
            out.setdefault(lv.kind, []).append(lv)
    return out
