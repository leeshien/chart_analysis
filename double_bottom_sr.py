"""Double bottoms found by extending the support/resistance machinery.

A double bottom IS a support level visited twice with a rally between, so this
module adds no shape matching. It reuses sr_major.episodes for the visits and
side-aware touches for the legs, which means the two bottoms are located by the
LOW rather than by a closing price.

Two consequences of anchoring on a level:

- There is no bottom-similarity knob. Two lows inside one band are equal by
  construction; measuring it separately, as the zigzag detector does, pays
  0.20 of a confidence score for information the level already carries.
- A breakdown can never masquerade as a second bottom. A bar whose low is far
  below the band is not a touch at all, so the episode simply does not exist.

The old close-based detector in patterns.py is left exactly as it is.
"""
from collections import namedtuple
from dataclasses import dataclass, field

import numpy as np

import sr_major
import swings

# score_level only reads .price and .kind, so a two-field stand-in is enough
# and avoids constructing a full levels.Level for a price that may not be one.
_Candidate = namedtuple("_Candidate", "price kind")


@dataclass
class Base:
    """One double bottom: two legs at a level, a neckline, and a verdict."""
    ticker: str
    timeframe: str
    source: str                 # "A" (stored levels) | "B" (derived lows)
    price: float                # the base level
    leg1_pos: int               # argmin(Low) position of bottom 1
    leg1_date: object
    leg1_price: float
    leg2_pos: int
    leg2_date: object
    leg2_price: float
    neckline: float
    neckline_pos: int
    prior_high: float
    rise: float                 # (neckline - price) / price
    retracement: float          # (neckline - leg1) / (prior_high - leg1)
    status: str                 # "confirmed" | "forming" | "broken"
    score: float
    sub_scores: dict = field(default_factory=dict)
    total_episodes: int = 0


def _leg(lows, span):
    """The lowest bar inside one episode: ``(position, low)``."""
    start, end = span
    pos = start + int(np.argmin(lows[start:end + 1]))
    return pos, float(lows[pos])


def leg_pairs(df, price, cfg, knobs):
    """Consecutive support-role episode pairs separated by a legal distance.

    Consecutiveness is what keeps "two legs" honest: if price returned to the
    base between them that is a third touch, and the pair straddling it is not
    a clean W. A triple bottom therefore yields two overlapping candidates,
    which suppression resolves later.

    Every episode is kept, including visits approached from below. Filtering
    the list by episode role also changed which episodes were ADJACENT, and so
    which pairs could form at all: measured over the index it blocked 49 valid
    bases while the reshuffle cost another 58. The case it existed to reject -
    a collapse through the level and a rally back to it - is now rejected
    directly by the interior rule in evaluate().
    """
    eps = sr_major.episodes(df, price, cfg, "support")
    lows = df["Low"].to_numpy(dtype=float)
    legs = [_leg(lows, e) for e in eps]

    pairs = []
    for i in range(len(legs) - 1):
        p1, p2 = legs[i][0], legs[i + 1][0]
        if knobs.leg_min_bars <= p2 - p1 <= knobs.leg_max_bars:
            pairs.append((p1, p2))
    return pairs


def episode_count(df, price, cfg):
    """Total visits at this level, reported so a trading range is visible.

    Not a filter: a level with six visits is a range rather than a base, but
    that cutoff belongs to whoever reads the scan output.
    """
    return len(sr_major.episodes(df, price, cfg, "support"))


# base is None unless reason == "ok". Returning the reason lets the scan report
# which rule removed each candidate, which is the whole diagnostic value of a
# funnel: a filter that never fires is dead weight, and one that removes
# everything is miscalibrated.
Verdict = namedtuple("Verdict", "base reason")


def evaluate(df, price, pair, cfg, knobs, ticker, timeframe, source,
             total_episodes):
    """Test one leg pair against the geometry, and decide its status.

    Legs are found with ``Low`` but confirmation needs a ``Close`` through the
    neckline. The asymmetry is deliberate and matches touch_detect: a bottom is
    where price traded down to, a breakout is a decision the market committed
    to.
    """
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    p1, p2 = pair

    interior = highs[p1 + 1:p2]
    if len(interior) == 0:
        return Verdict(None, "no_interior")
    neckline_pos = p1 + 1 + int(np.argmax(interior))
    neckline = float(highs[neckline_pos])

    # The base must HOLD between the legs. This is the thesis stated directly:
    # a W is two tests of a level that was never abandoned in between, while a
    # collapse through the level and a rally back to it is a resistance retest
    # wearing a double bottom's shape.
    tol = abs(price) * cfg.sr_touch_tol_pct
    if float(closes[p1:p2 + 1].min()) < price - tol:
        return Verdict(None, "base_broke")

    rise = (neckline - price) / price
    if rise < knobs.rise_floor:
        return Verdict(None, "rise")

    leg1_price, leg2_price = float(lows[p1]), float(lows[p2])
    # All history before leg 1, not a fixed window: where the prior high sits
    # does not matter, only that the pattern forms below it.
    prior_high = float(highs[:p1 + 1].max())
    decline = prior_high - leg1_price
    if decline <= 0:
        return Verdict(None, "no_decline")

    # The one structural rule left: a neckline at or above the prior high is
    # not a double bottom, because price made a new high between the two lows.
    if neckline >= prior_high:
        return Verdict(None, "retrace")

    # Kept for reporting and for the shape term, no longer a gate.
    retracement = (neckline - leg1_price) / decline

    # Status, in the order the events happened. A base that broke and only
    # later cleared the old neckline is not a confirmed double bottom.
    after = closes[p2 + 1:]
    broke = np.nonzero(after < price - tol)[0]
    cleared = np.nonzero(after > neckline)[0]
    broke_at = int(broke[0]) if len(broke) else None
    cleared_at = int(cleared[0]) if len(cleared) else None
    if broke_at is not None and (cleared_at is None or broke_at < cleared_at):
        status = "broken"
    elif cleared_at is not None:
        status = "confirmed"
    else:
        status = "forming"

    base = Base(
        ticker=ticker, timeframe=timeframe, source=source, price=float(price),
        leg1_pos=p1, leg1_date=df.index[p1].date(), leg1_price=leg1_price,
        leg2_pos=p2, leg2_date=df.index[p2].date(), leg2_price=leg2_price,
        neckline=neckline, neckline_pos=neckline_pos, prior_high=prior_high,
        rise=float(rise), retracement=float(retracement), status=status,
        score=0.0, sub_scores={}, total_episodes=total_episodes,
    )
    return Verdict(base, "ok")


# The level term is the point of the whole exercise: episodes, reaction, band
# volume, span and flip arrive as one number that has already been eyeballed
# across the index, rather than being re-derived here.
WEIGHTS = {
    "level": 0.30,
    "breakout": 0.20,
    "duration": 0.15,
    "depth": 0.15,
    "shape": 0.10,
    "legs": 0.10,
}

_BREAKOUT_FULL = 0.05        # 5% above the neckline earns the full term
_FUNNEL_KEYS = ("candidates", "no_pairs", "no_interior", "base_broke", "rise",
                "no_decline", "retrace", "broken", "kept")


def new_funnel():
    """Zeroed rejection counters, so a scan can report what removed what."""
    return {k: 0 for k in _FUNNEL_KEYS}


def score(df, base, cfg, knobs):
    """Fill in ``base.sub_scores`` and ``base.score``. Mutates and returns it."""
    level = sr_major.score_level(df, _Candidate(base.price, "support"), cfg)

    span = max(1, knobs.leg_max_bars - knobs.leg_min_bars)
    duration = (base.leg2_pos - base.leg1_pos - knobs.leg_min_bars) / span

    depth = base.rise / (2.0 * knobs.rise_floor) if knobs.rise_floor > 0 else 1.0

    # Headroom: how much of the decline is still unrecovered above the
    # neckline. With the band gone this is the useful thing retracement says -
    # a breakout with room left to the prior high beats one into overhead
    # supply.
    shape = 1.0 - base.retracement

    tol = abs(base.price) * cfg.sr_touch_tol_pct
    legs = 1.0 - abs(base.leg1_price - base.leg2_price) / max(1e-9, 2.0 * tol)

    breakout = 0.0
    if base.status == "confirmed":
        last = float(df["Close"].iloc[-1])
        breakout = (last - base.neckline) / base.neckline / _BREAKOUT_FULL

    sub = {"level": level["score"], "breakout": breakout, "duration": duration,
           "depth": depth, "shape": shape, "legs": legs}
    sub = {k: float(np.clip(v, 0.0, 1.0)) for k, v in sub.items()}

    base.sub_scores = sub
    base.score = float(np.clip(sum(WEIGHTS[k] * sub[k] for k in WEIGHTS), 0.0, 1.0))
    return base


def suppress(bases):
    """Keep the best base among any whose bar windows overlap.

    Deliberately not patterns.suppress_overlaps: that one keys on a ``pattern``
    field and a date window, while these overlap in bar positions and carry no
    pattern name. Six lines here beats three alias fields on Base.
    """
    kept = []
    for b in sorted(bases, key=lambda x: -x.score):
        if any(b.leg1_pos <= k.leg2_pos and k.leg1_pos <= b.leg2_pos
               for k in kept):
            continue
        kept.append(b)
    return kept


def detect(df, prices, cfg, knobs, ticker, timeframe, source, funnel=None):
    """Every double bottom at these candidate prices, best first.

    ``broken`` bases are counted in the funnel and then dropped: a base that
    already failed is history, not a setup.
    """
    if funnel is None:
        funnel = new_funnel()
    else:
        # Fill in place rather than replacing: the caller reads its own dict
        # back to accumulate totals, so a fresh dict would lose every count.
        for key in _FUNNEL_KEYS:
            funnel.setdefault(key, 0)
    found = []
    for price in prices:
        funnel["candidates"] += 1
        pairs = leg_pairs(df, price, cfg, knobs)
        if not pairs:
            funnel["no_pairs"] += 1
            continue
        total = episode_count(df, price, cfg)
        for pair in pairs:
            verdict = evaluate(df, price, pair, cfg, knobs, ticker, timeframe,
                               source, total)
            if verdict.base is None:
                funnel[verdict.reason] += 1
                continue
            if verdict.base.status == "broken":
                funnel["broken"] += 1
                continue
            found.append(score(df, verdict.base, cfg, knobs))
    kept = suppress(found)
    funnel["kept"] += len(kept)
    return kept


def candidates_from_levels(level_rows):
    """Pass A: support prices Gemini already named, from the levels table.

    Free and reuses output that has been eyeballed across the index. The limit
    is recall: the model returns about 5.4 levels per chart, so a base it did
    not name cannot be found this way.
    """
    return sorted(float(r["price"]) for r in level_rows
                  if r.get("kind") == "support")


def candidates_from_lows(df, cfg, knobs):
    """Pass B: candidate bases implied by pairs of swing lows.

    Independent of what the model chose to name, so recall is higher. Each
    surviving pair's mean becomes a candidate price, which the shared evaluator
    then has to justify through the same episode machinery as pass A.
    """
    troughs = swings.find_swings(df, cfg)["troughs"]
    lows = df["Low"].to_numpy(dtype=float)
    # Both lows must fit inside one band of half-width tol, so they may differ
    # by at most twice it.
    spread = 2.0 * cfg.sr_touch_tol_pct

    prices = set()
    for i, a in enumerate(troughs):
        for b in troughs[i + 1:]:
            if not (knobs.leg_min_bars <= b - a <= knobs.leg_max_bars):
                continue
            la, lb = lows[a], lows[b]
            if min(la, lb) <= 0 or abs(la - lb) / min(la, lb) > spread:
                continue
            prices.add(round((la + lb) / 2.0, 4))
    return sorted(prices)
