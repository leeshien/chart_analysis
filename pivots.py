from dataclasses import dataclass


@dataclass
class Pivot:
    pos: int          # integer row position in the weekly df
    date: object      # datetime.date
    price: float
    kind: str         # "H" | "L"


def zigzag_pivots(df, cfg):
    """Return an alternating list of High/Low Pivots using a percentage-reversal
    ZigZag computed on the CLOSING price (line chart). Percentage-based, so it is
    scale-invariant."""
    price = df["Close"].to_numpy(dtype=float)
    idx = df.index
    n = len(df)
    pct = cfg.zigzag_pct

    pivots = []
    if n == 0:
        return pivots

    def _add(pos, kind, p):
        if pivots and pivots[-1].pos == pos:
            return
        pivots.append(Pivot(pos, idx[pos].date(), float(p), kind))

    direction = 0                 # 0 unknown, 1 up-leg (seek High), -1 down-leg
    hi, hi_pos = price[0], 0      # running max / min while direction is unknown
    lo, lo_pos = price[0], 0
    ext, ext_pos = price[0], 0    # tracked extreme of the current leg

    for i in range(1, n):
        p = price[i]
        if direction == 0:
            if p > hi:
                hi, hi_pos = p, i
            if p < lo:
                lo, lo_pos = p, i
            # Establish the initial leg without emitting the starting anchor.
            if (p - lo) / lo >= pct:
                direction = 1              # rising -> seek a High
                ext, ext_pos = hi, hi_pos
            elif (hi - p) / hi >= pct:
                direction = -1             # falling -> seek a Low
                ext, ext_pos = lo, lo_pos
        elif direction == 1:
            if p > ext:
                ext, ext_pos = p, i
            elif (ext - p) / ext >= pct:
                _add(ext_pos, "H", ext)
                direction = -1
                ext, ext_pos = p, i
        else:  # direction == -1
            if p < ext:
                ext, ext_pos = p, i
            elif (p - ext) / ext >= pct:
                _add(ext_pos, "L", ext)
                direction = 1
                ext, ext_pos = p, i

    # final (tentative) pivot = the running extreme of the last leg
    if direction == 1:
        _add(ext_pos, "H", ext)
    elif direction == -1:
        _add(ext_pos, "L", ext)

    return pivots
