from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless, no display needed
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402

_SUPPORT = "#16a34a"
_RESISTANCE = "#dc2626"


def _draw_levels(ax, levels, extras=None):
    """Draw the levels as labelled horizontal lines on the price axis.

    A level the model flagged ``major`` is drawn as a heavy SOLID line, so the one
    support and one resistance that matter most are separable at a glance from the
    dashed minor levels.
    """
    for i, lv in enumerate(levels or []):
        color = _RESISTANCE if lv.kind == "resistance" else _SUPPORT
        extra = (extras[i] if extras and i < len(extras) else None) or {}
        # An unsnapped model price sits between real bars — dotted, to show it was
        # left where the model put it rather than pulled onto a real level.
        unsnapped = extra.get("snapped") is False
        # Three tiers, each carrying one idea: SOLID means the level cleared the
        # major floor, THICKNESS means it ranked first of its kind. A chart can have
        # several major supports, so solidity is not rationed to one line.
        major = bool(extra.get("computed_major"))
        primary = bool(extra.get("computed_primary"))
        model_major = bool(extra.get("major"))

        if primary:
            style, width, alpha, z = "-", 2.8, 0.95, 6
        elif major:
            style, width, alpha, z = "-", 1.6, 0.80, 5
        else:
            style, width, alpha, z = (":" if unsnapped else "--"), \
                0.8 + 1.6 * lv.strength, 0.55, 3
        ax.axhline(lv.price, color=color, alpha=alpha, lw=width, ls=style, zorder=z)

        label = f"{lv.price:,.2f} ({lv.touches})" + ("*" if unsnapped else "")
        eps = extra.get("episodes")
        if eps is not None:
            label += f" {eps}ep"
        tag = "R" if lv.kind == "resistance" else "S"
        if primary:
            label = f"MAJOR {tag}  " + label
        elif major:
            label = f"major {tag.lower()}  " + label
        if extra.get("nearest_major_support"):
            label += "  <- risk"
        if model_major and not major:
            label += "  [model major]"
        ax.annotate(
            label,
            xy=(1.0, lv.price), xycoords=("axes fraction", "data"),
            xytext=(3, 0), textcoords="offset points",
            fontsize=7.5 if major else 7, color=color, va="center", ha="left",
            fontweight="bold" if major else "normal",
        )


def render_levels(df, levels, out_path, ticker="", extras=None, title=None,
                  figsize=(14, 8), fmt="svg", marks=None):
    """One candlestick panel with a volume panel beneath it, plus the S/R lines.

    Drawn through mplfinance's own figure rather than external axes, which is what
    makes the volume panel available — mplfinance manages the price/volume split
    itself and will not do it into an axis you hand it.

    Level labels read ``price (touches) Nep``, where Nep is the number of distinct
    visits. A trailing ``*`` and a dotted line mark a model price too far from any
    real level to snap.

    Three tiers: every level clearing the major floor is SOLID, prefixed ``major
    s`` / ``major r``; the strongest of each kind is thicker and prefixed ``MAJOR
    S`` / ``MAJOR R``; minor levels stay dashed. The nearest major support below
    the close is tagged ``<- risk``, and a level the model called major but the
    arithmetic did not is tagged ``[model major]``.

    ``marks`` optionally points at individual bars: each is a dict with ``pos``
    (integer bar position), ``price``, and an optional ``label`` and ``color``.
    A breakout chart is unreadable without this - the ceiling is drawn across
    the whole width, so nothing says which candle actually went through it.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = mpf.plot(
        df, type="candle", volume=True, style="yahoo",
        figsize=figsize, returnfig=True, tight_layout=True,
        panel_ratios=(3, 1), datetime_format="%Y-%m",
        title=title if title is not None else ticker,
        warn_too_much_data=len(df) + 1,
    )

    _draw_levels(axes[0], levels, extras)
    _draw_marks(axes[0], marks)

    fig.savefig(out_path, format=fmt, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _draw_marks(ax, marks):
    """Point at specific bars: a vertical guide, an arrow under the candle.

    The arrow sits below the bar rather than on it so it cannot be mistaken for
    part of the price action, and the vertical guide carries the eye down to
    the volume panel where the break-bar volume is.
    """
    if not marks:
        return
    lo, hi = ax.get_ylim()
    for m in marks:
        colour = m.get("color", "#7c3aed")
        pos, price = m["pos"], float(m["price"])
        ax.axvline(pos, color=colour, ls=":", lw=1.2, alpha=0.75, zorder=4)
        ax.annotate("", xy=(pos, price), xytext=(pos, price - 0.06 * (hi - lo)),
                    arrowprops=dict(arrowstyle="-|>", color=colour, lw=2.0),
                    zorder=7)
        if m.get("label"):
            # A break is usually the newest bar, so a centred label runs off
            # the right edge and into the level annotations. Flip it left when
            # the bar is in the last fifth of the chart.
            x0, x1 = ax.get_xlim()
            near_right = (pos - x0) / max(1e-9, x1 - x0) > 0.8
            ax.annotate(m["label"],
                        xy=(pos, price - 0.07 * (hi - lo)),
                        xytext=(-10 if near_right else 0, 0),
                        textcoords="offset points",
                        ha="right" if near_right else "center", va="top",
                        fontsize=8, color=colour, fontweight="bold", zorder=8,
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                                  edgecolor=colour, alpha=0.95))
    # Headroom so an arrow under a low bar is not clipped away.
    ax.set_ylim(lo - 0.10 * (hi - lo), hi)
