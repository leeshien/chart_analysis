import numpy as np
from scipy.signal import find_peaks


def find_swings(df, cfg) -> dict:
    """Return dict with 'peaks' and 'troughs' as lists of integer positions."""
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)

    price_range = float(np.nanmax(high) - np.nanmin(low))
    prominence = cfg.swing_prominence_pct * price_range if price_range > 0 else None

    peak_idx, _ = find_peaks(high, distance=cfg.swing_distance_weeks, prominence=prominence)
    trough_idx, _ = find_peaks(-low, distance=cfg.swing_distance_weeks, prominence=prominence)

    return {"peaks": peak_idx.tolist(), "troughs": trough_idx.tolist()}
