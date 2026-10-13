"""Measure which level is the *major* support and which the major resistance.

The model is asked for its own opinion (the ``major`` flag in sr_parse), but that is
a judgement and cannot be explained or reproduced. This module computes the answer
from the price data instead, so the choice is defensible column by column.

The design point is that a touch is an EVENT, not a bar. Counting bars rewards a
long sideways consolidation — twenty weeks parked at one price scores twenty
"touches" for what is really a single visit — while a level tested four separate
times years apart, rejecting price hard each time, scores fewer. Episodes fix that.
"""
import numpy as np


def _touch_mask(df, price, cfg, kind=None):
    """Bars that reached ``price`` within sr_touch_tol_pct.

    The default counts a bar whenever the level falls anywhere inside
    [Low, High]. Through a long consolidation that rewards mid-range prices,
    which are exactly where support and resistance are not — on a 2.5-year daily
    chart it put MSFT's primary support in the middle of a nine-month range.

    With ``cfg.sr_side_aware_touches`` set and a ``kind`` supplied, a support is
    instead tested by the LOW turning near the level and a resistance by the
    HIGH, which is what defines one. Duplicated from sr_v2 rather than imported,
    because sr_v2 imports from this module and the cycle would not resolve.
    """
    tol = abs(price) * cfg.sr_touch_tol_pct
    lows = df["Low"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)

    if kind and getattr(cfg, "sr_side_aware_touches", False):
        closes = df["Close"].to_numpy(dtype=float)
        if kind == "resistance":
            return (np.abs(highs - price) <= tol) & (closes <= price + tol)
        return (np.abs(lows - price) <= tol) & (closes >= price - tol)

    return (lows <= price + tol) & (highs >= price - tol)


def episodes(df, price, cfg, kind=None) -> list:
    """Group touching bars into distinct visits.

    A run of consecutive touching bars is one episode. Price must stay clear of the
    band for sr_episode_gap_weeks before a new episode can open, so a level that is
    brushed repeatedly while consolidating counts once, not once per bar.

    Returns a list of inclusive ``(start_pos, end_pos)`` pairs.
    """
    if price is None or not np.isfinite(price) or price <= 0 or len(df) == 0:
        return []
    mask = _touch_mask(df, price, cfg, kind)
    gap = cfg.sr_episode_gap_weeks

    eps = []
    for i, touching in enumerate(mask):
        if not touching:
            continue
        if eps and i - eps[-1][1] <= gap:
            eps[-1][1] = i            # same visit, still within the gap
        else:
            eps.append([i, i])
    return [(a, b) for a, b in eps]


def _episode_role(df, price, start, cfg):
    """Whether price approached this episode from above or below.

    Approaching from above and holding means the level acted as support; from below,
    resistance. Judged on the mean close over the bars preceding the episode.
    """
    closes = df["Close"].to_numpy(dtype=float)
    lookback = max(1, cfg.sr_episode_gap_weeks)
    prior = closes[max(0, start - lookback):start]
    if len(prior) == 0:
        return "support" if closes[start] >= price else "resistance"
    return "support" if float(np.mean(prior)) >= price else "resistance"


def reaction(df, price, eps, cfg) -> float:
    """Median move away from the level after each episode, as a fraction of price.

    A level that repeatedly launched 20% moves is major; one that produced 3%
    wiggles is decoration however often it was touched. The median keeps a single
    dramatic bounce from carrying an otherwise weak level.

    Looks forward from each episode, so this is descriptive only — never reuse it
    in a backtest, where it would leak future information.
    """
    if not eps or price <= 0:
        return 0.0
    closes = df["Close"].to_numpy(dtype=float)
    window = cfg.sr_reaction_window

    moves = []
    for start, end in eps:
        after = closes[end + 1:end + 1 + window]
        if len(after) == 0:
            continue
        role = _episode_role(df, price, start, cfg)
        if role == "support":
            excursion = (float(after.max()) - price) / price
        else:
            excursion = (price - float(after.min())) / price
        moves.append(max(0.0, excursion))
    return float(np.median(moves)) if moves else 0.0


def band_volume_share(df, price, cfg) -> float:
    """Share of all volume that changed hands inside the level's band.

    The "trapped positions" argument for why levels have memory: the more stock
    that actually transacted at a price, the more participants care about it.
    """
    vol = df["Volume"].to_numpy(dtype=float)
    total = float(vol.sum())
    if total <= 0 or price <= 0:
        return 0.0
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    typical = (highs + lows + closes) / 3.0
    tol = abs(price) * cfg.sr_touch_tol_pct
    inside = (typical >= price - tol) & (typical <= price + tol)
    return float(vol[inside].sum() / total)


def has_flipped(df, price, eps, cfg) -> bool:
    """True if the level acted as both support and resistance across its episodes.

    A level that capped price, broke, then held on the retest is the textbook major
    level — rare, and strong evidence.
    """
    roles = {_episode_role(df, price, start, cfg) for start, _ in eps}
    return len(roles) > 1


def score_level(df, level, cfg) -> dict:
    """Score one level on episodes, reaction, volume, span and polarity flip.

    Returns the component scores alongside the total, so a chart's choice of major
    line can be explained from the report rather than taken on trust.
    """
    price = float(level.price)
    eps = episodes(df, price, cfg, level.kind)
    n = max(1, len(df) - 1)
    w = cfg.sr_major_weights

    ep_score = min(len(eps) / float(cfg.sr_episode_saturation), 1.0)
    react = reaction(df, price, eps, cfg)
    react_score = min(react / cfg.sr_reaction_full, 1.0) if cfg.sr_reaction_full > 0 else 0.0
    vol_share = band_volume_share(df, price, cfg)
    vol_score = min(vol_share / cfg.sr_volume_share_full, 1.0) if cfg.sr_volume_share_full > 0 else 0.0
    span = (eps[-1][1] - eps[0][0]) / n if eps else 0.0
    flip = has_flipped(df, price, eps, cfg)

    total = (w["episodes"] * ep_score + w["reaction"] * react_score
             + w["volume"] * vol_score + w["span"] * span
             + w["flip"] * (1.0 if flip else 0.0))

    return {
        "episodes": len(eps),
        "reaction": react,
        "volume_share": vol_share,
        "span": span,
        "flip": flip,
        "score": float(np.clip(total, 0.0, 1.0)),
    }


def pick_majors(df, levels, cfg) -> tuple:
    """Find every major level, and rank the strongest of each kind first.

    Returns ``(majors, primary, scores)``:

    - ``majors`` maps kind -> list of level indices clearing both floors, best first.
      A chart can have several genuinely major supports; clearing the bar is a
      property of the level, so the count is whatever the data says, including zero.
    - ``primary`` maps kind -> the single strongest index. This is a *ranking*, the
      answer to "which line first", and is deliberately separate from being major.
    - ``scores`` is the per-level score dict, aligned with ``levels``.
    """
    scores = [score_level(df, lv, cfg) for lv in levels]
    majors, primary = {}, {}
    for kind in ("support", "resistance"):
        eligible = [i for i, lv in enumerate(levels)
                    if lv.kind == kind
                    and scores[i]["episodes"] >= cfg.sr_major_min_episodes
                    and scores[i]["score"] >= cfg.sr_major_min_score]
        eligible.sort(key=lambda i: scores[i]["score"], reverse=True)
        if eligible:
            majors[kind] = eligible
            primary[kind] = eligible[0]
    return majors, primary, scores


def nearest_major_support(levels, majors, last_close):
    """Index of the highest-priced major support still below the close.

    The risk reference, and a different question from "strongest" — it is simply
    the closest major floor underneath current price. Returns None if there is none.
    """
    below = [i for i in majors.get("support", []) if levels[i].price < last_close]
    if not below:
        return None
    return max(below, key=lambda i: levels[i].price)


def annotate(df, levels, extras, cfg):
    """Write the computed verdict into ``extras``, preserving the model's own flag.

    Each extra gains ``computed_major`` (cleared the floor), ``computed_primary``
    (strongest of its kind), ``nearest_major_support`` (the risk reference), the
    score components, and ``major_agrees`` comparing the computation against the
    model's ``major``. Both opinions are kept so one run shows where they differ.
    """
    while len(extras) < len(levels):
        extras.append({})

    majors, primary, scores = pick_majors(df, levels, cfg)
    major_set = {i for idxs in majors.values() for i in idxs}
    primary_set = set(primary.values())
    last_close = float(df["Close"].iloc[-1])
    nearest = nearest_major_support(levels, majors, last_close)

    for i, extra in enumerate(extras[:len(levels)]):
        s = scores[i]
        extra["computed_major"] = i in major_set
        extra["computed_primary"] = i in primary_set
        extra["nearest_major_support"] = (i == nearest)
        extra["major_score"] = round(s["score"], 3)
        extra["episodes"] = s["episodes"]
        extra["reaction"] = round(s["reaction"], 4)
        extra["volume_share"] = round(s["volume_share"], 4)
        extra["flip"] = s["flip"]
        extra["major_agrees"] = bool(extra.get("major")) == (i in major_set)
    return levels, extras, majors, primary
