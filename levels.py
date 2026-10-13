from dataclasses import dataclass

import numpy as np


@dataclass
class Level:
    price: float
    kind: str            # "support" | "resistance" (vs latest close)
    strength: float      # 0..1
    touches: int
    zone_low: float
    zone_high: float
    first_date: object
    last_date: object


def count_touches(df, price, cfg) -> int:
    """Weekly bars whose [Low, High] range reaches ``price`` within sr_touch_tol_pct.

    One shared definition of a "touch", used for both the histogram levels and the
    model-supplied ones, so a touch count means the same thing on either side of a
    side-by-side comparison. Unlike the old pivot count it does not depend on
    zigzag_pct, so a level tested by shallow reversals is still counted.
    """
    if price is None or not np.isfinite(price) or price <= 0:
        return 0
    tol = abs(price) * cfg.sr_touch_tol_pct
    lows = df["Low"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    return int(np.count_nonzero((lows <= price + tol) & (highs >= price - tol)))


def _bin_edges(lo, hi, cfg):
    """Bin edges spanning [lo, hi].

    Log spacing gives every bin a constant *percentage* width, so a level near 20
    and one near 200 get proportionally sized zones. Linear spacing makes the low
    end coarse and the high end fragmented on any chart that trended a lot.
    """
    bins = cfg.sr_bins
    if getattr(cfg, "sr_bin_scale", "log") == "log" and lo > 0 and hi > lo:
        return np.geomspace(lo, hi, bins + 1)
    return np.linspace(lo, hi, bins + 1)


def _price_histogram(df, cfg):
    """Volume-by-price histogram over the price range. Returns (edges, weights)."""
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    vol = df["Volume"].to_numpy(dtype=float)

    typical = (highs + lows + closes) / 3.0
    lo, hi = float(lows.min()), float(highs.max())
    bins = cfg.sr_bins
    edges = _bin_edges(lo, hi, cfg)
    if hi <= lo:
        return edges, np.zeros(bins)

    which = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    w = vol if cfg.sr_volume_weighted else np.ones_like(vol)
    weights = np.zeros(bins)
    for i, b in enumerate(which):
        weights[b] += w[i]
    return edges, weights


def _zone_peaks(weights, cfg):
    """Runs of adjacent local-maximum bins at or above the density threshold.

    Returned as inclusive ``(start, end)`` index pairs. A plateau of equal-weight
    bins satisfies the ``>=`` test on both sides and used to emit one level per
    bin; grouping adjacent peaks collapses it into a single zone.
    """
    n = len(weights)
    if n == 0 or weights.max() <= 0:
        return []
    thresh = cfg.sr_zone_min_frac * weights.max()

    peaks = []
    for i in range(n):
        if weights[i] < thresh:
            continue
        left = weights[i - 1] if i > 0 else -1.0
        right = weights[i + 1] if i < n - 1 else -1.0
        if weights[i] >= left and weights[i] >= right:
            peaks.append(i)

    runs = []
    for i in peaks:
        if runs and i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a, b) for a, b in runs]


def _merge_close(levels, cfg):
    """Collapse levels priced within sr_merge_pct of each other.

    Two neighbouring zones can each yield a level a fraction of a percent apart,
    which draws as two lines where the chart has one. The stronger price wins and
    its zone widens to cover both.
    """
    if not levels:
        return []
    ordered = sorted(levels, key=lambda lv: lv.price)
    merged = [ordered[0]]
    for lv in ordered[1:]:
        prev = merged[-1]
        ref = max(abs(prev.price), 1e-9)
        if abs(lv.price - prev.price) / ref <= cfg.sr_merge_pct:
            keep = prev if prev.strength >= lv.strength else lv
            merged[-1] = Level(
                keep.price, keep.kind,
                max(prev.strength, lv.strength),
                max(prev.touches, lv.touches),
                min(prev.zone_low, lv.zone_low),
                max(prev.zone_high, lv.zone_high),
                min(prev.first_date, lv.first_date),
                max(prev.last_date, lv.last_date),
            )
        else:
            merged.append(lv)
    return merged


def find_levels(df, pivots, cfg):
    """Detect S/R levels: histogram zones (B) refined to exact pivot prices (A)."""
    edges, weights = _price_histogram(df, cfg)
    runs = _zone_peaks(weights, cfg)
    if not runs:
        return []

    closes = df["Close"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    vol = df["Volume"].to_numpy(dtype=float)
    typical = (highs + lows + closes) / 3.0

    last_close = float(closes[-1])
    wmax = float(weights.max())
    span_total = max(1, len(df) - 1)
    dates_all = list(df.index.date)

    levels = []
    for a, b in runs:
        zlow, zhigh = float(edges[a]), float(edges[b + 1])
        density = float(weights[a:b + 1].max()) / wmax if wmax > 0 else 0.0
        zpivots = [p for p in pivots if zlow <= p.price <= zhigh]

        if zpivots:
            if len(zpivots) >= cfg.sr_min_touches:
                price = float(np.median([p.price for p in zpivots]))
            else:
                price = (zlow + zhigh) / 2.0  # weaker fallback
            positions = [p.pos for p in zpivots]
            dates = [p.date for p in zpivots]
        else:
            # No zigzag pivot landed here. The zone survives only as a genuinely
            # dense volume shelf, priced at the volume-weighted centre of the bars
            # that traded in it rather than at the bin midpoint.
            if density < cfg.sr_shelf_min_density:
                continue
            in_zone = (typical >= zlow) & (typical <= zhigh)
            if not in_zone.any():
                continue
            zvol = vol[in_zone]
            price = float(np.average(typical[in_zone], weights=zvol)
                          if zvol.sum() > 0 else typical[in_zone].mean())
            positions = np.flatnonzero(in_zone).tolist()
            dates = [d for d, keep in zip(dates_all, in_zone) if keep]

        touches = count_touches(df, price, cfg)
        recency = max(positions) / span_total
        time_span = (max(positions) - min(positions)) / span_total

        touch_score = min(touches / float(cfg.sr_touch_saturation), 1.0)
        strength = float(np.clip(
            0.4 * density + 0.3 * touch_score + 0.2 * recency + 0.1 * time_span, 0.0, 1.0))

        kind = "support" if price < last_close else "resistance"
        levels.append(Level(price, kind, strength, touches, zlow, zhigh,
                            min(dates), max(dates)))

    levels = _merge_close(levels, cfg)
    levels.sort(key=lambda lv: lv.strength, reverse=True)
    return levels[:cfg.sr_max_levels]


def annotate_levels(matches, df, levels, cfg):
    """Annotate each match with the nearest overhead resistance (above the
    breakout, within sr_overhead_pct — applies a confidence penalty) and the
    nearest support below the latest close (a risk / stop reference)."""
    last_close = float(df["Close"].to_numpy(dtype=float)[-1])
    resistances = sorted((l for l in levels if l.kind == "resistance"), key=lambda l: l.price)
    supports = sorted((l for l in levels if l.kind == "support"), key=lambda l: l.price, reverse=True)

    for m in matches:
        breakout_level = m.neckline if m.neckline is not None else last_close

        overhead = None
        for lv in resistances:                       # ascending
            if lv.price > breakout_level:
                if (lv.price - breakout_level) / breakout_level <= cfg.sr_overhead_pct:
                    overhead = lv.price
                break                                # only the nearest one matters
        m.overhead_resistance = overhead
        if overhead is not None:
            m.confidence = float(max(0.0, m.confidence - cfg.sr_overhead_penalty))

        nsup = None
        for lv in supports:                          # descending
            if lv.price < last_close:
                nsup = lv.price
                break
        m.nearest_support = nsup

    return matches
