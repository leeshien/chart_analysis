from dataclasses import dataclass, field


@dataclass
class Config:
    # zigzag
    zigzag_pct: float = 0.08          # reversal % that confirms a pivot

    # double bottom
    db_similarity_tol: float = 0.06   # full score near-equal bottoms; 0 by 6%
    db_min_neckline_rise: float = 0.10
    db_prior_downtrend: float = 0.15
    db_min_weeks: int = 20
    db_max_weeks: int = 200           # allow bases up to ~2.3 years

    # cup and handle
    cup_rim_tolerance: float = 0.10   # left/right rims rarely match within 6%
    cup_depth_min: float = 0.12
    cup_depth_max: float = 0.50
    cup_min_weeks: int = 60
    cup_max_weeks: int = 250          # allow cups up to ~3 years
    cup_prior_uptrend: float = 0.30
    handle_depth_max: float = 0.15
    handle_max_weeks: int = 8
    cup_overshoot_tol: float = 0.03   # max close above the rim during the cup
    cup_min_roundedness: float = 0.35  # reject non-rounded (V) bases

    # scoring weights (each dict sums to 1.0)
    db_weights: dict = field(default_factory=lambda: {
        "similarity": 0.20, "depth": 0.15, "prior_trend": 0.10,
        "duration": 0.25, "breakout": 0.30,          # longer base -> higher score
    })
    cup_weights: dict = field(default_factory=lambda: {
        "rim_symmetry": 0.10, "depth": 0.10, "roundedness": 0.25,
        "handle": 0.10, "prior_trend": 0.10, "duration": 0.20, "breakout": 0.15,
    })

    # vlm (vision-language detector)
    vlm_base_url: str = "http://localhost:8080/v1"
    vlm_model: str = "qwen3.5-vl"
    vlm_timeout: int = 120            # seconds per image (9B on 10GB is slow)
    vlm_temperature: float = 0.0
    vlm_max_image_width: int = 896    # px, caps image tokens / VRAM
    vlm_min_confidence: float = 0.5   # report threshold

    # claude (cloud vision-language detector — alternative to the local vlm)
    claude_model: str = "claude-sonnet-5"
    claude_max_tokens: int = 2048
    claude_max_image_width: int = 1000  # px; cloud has no VRAM limit, so bigger
    claude_min_confidence: float = 0.5  # report threshold

    # gemini (cloud vlm — Google)
    gemini_model: str = "gemini-3.7-flash"
    gemini_max_tokens: int = 4096     # levels + two pattern verdicts truncate at 2048
    gemini_max_image_width: int = 1000
    gemini_min_confidence: float = 0.5
    gemini_sr_max_levels: int = 6     # max S/R lines requested from the model

    # openai (cloud vlm — OpenAI)
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 2048
    openai_max_image_width: int = 1000
    openai_min_confidence: float = 0.5

    # support / resistance
    sr_bins: int = 50                 # price histogram bins
    sr_volume_weighted: bool = True   # volume-by-price (else bar-count)
    sr_zone_min_frac: float = 0.30    # bin weight >= 30% of max to be a zone peak
    sr_max_levels: int = 6            # keep top-N strongest levels
    sr_min_touches: int = 2           # pivots needed to pin an exact level
    sr_overhead_pct: float = 0.15     # resistance within 15% above breakout = overhead
    sr_overhead_penalty: float = 0.10  # confidence penalty when overhead resistance present
    sr_bin_scale: str = "log"         # "log" (constant % bin width) | "linear"
    sr_merge_pct: float = 0.02        # collapse levels priced within 2% of each other
    sr_touch_tol_pct: float = 0.02    # a bar touches a level if it reaches within 2%
    sr_shelf_min_density: float = 0.60  # pivot-less zone must be this dense to survive
    sr_touch_saturation: int = 12     # touch count at which touch_score reaches 1.0
    sr_snap_max_pct: float = 0.03     # furthest a model price may be snapped to real data
    sr_price_range_margin: float = 0.05  # chart-range widening for the hallucination guard

    # major level scoring (computed from the data, not the model's opinion)
    sr_episode_gap_weeks: int = 4     # weeks clear of the band before a new visit counts
    sr_episode_saturation: int = 4    # distinct visits at which the episode term maxes
    sr_reaction_window: int = 13      # weeks looked ahead to measure the move away
    sr_reaction_full: float = 0.20    # a 20% move away scores full marks
    sr_volume_share_full: float = 0.15  # 15% of all volume in one band scores full marks
    sr_major_min_episodes: int = 2    # a level visited once is never major
    sr_major_min_score: float = 0.55  # below this, the chart has no major level of that kind
    # Side-aware touches: a support is tested by the LOW, a resistance by the
    # HIGH. Off by default so scan_sr and the weekly path keep v1 semantics.
    sr_side_aware_touches: bool = False
    sr_major_weights: dict = field(default_factory=lambda: {
        "episodes": 0.30, "reaction": 0.30, "volume": 0.20, "span": 0.15, "flip": 0.05,
    })

    # swing detection (scipy find_peaks), used by the double-bottom pass that
    # derives candidate bases from the lows instead of from the model's levels.
    # swings.py has referenced both of these since it was written; neither
    # existed, so calling find_swings raised AttributeError.
    swing_prominence_pct: float = 0.03  # min prominence as a fraction of the range
    swing_distance_weeks: int = 4       # BARS between distinct swings, scaled per timeframe

    # output
    min_confidence: float = 0.35


DEFAULT = Config()
