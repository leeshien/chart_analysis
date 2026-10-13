from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import numpy as np  # noqa: E402


def _nearest_pos(df, date_str):
    """Map a model-supplied 'YYYY-MM-DD' date to the closest bar's row position.

    The model reads dates approximately off the axis, so an exact match is rare —
    snap to the nearest weekly bar. Returns None if the date can't be parsed.
    """
    try:
        target = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    best_i, best_diff = None, None
    for i, d in enumerate(df.index.date):
        diff = abs((d - target).days)
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i


def _snap_price(row, price):
    """Snap a reported price to the bar's nearest OHLC value, so the marker lands
    on the candle instead of floating if the model misread the y-value."""
    return min((row["Open"], row["High"], row["Low"], row["Close"]),
               key=lambda c: abs(c - price))


def render_line(df, out_path, max_width_px=896, dpi=100):
    """Clean closing-price LINE chart PNG — the image fed to the VLM.

    A single line (no candles, no volume) is easiest for a vision model to read
    the overall pattern shape.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    width_in = max_width_px / dpi
    height_in = width_in * 0.6
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    ax.plot(df.index, df["Close"], color="#111827", lw=1.5)
    ax.set_ylabel("Price")
    # gridlines + axis labels help the model reason about the shape/scale
    ax.grid(True, which="major", alpha=0.35)
    ax.minorticks_on()
    ax.grid(True, which="minor", alpha=0.15)
    ax.margins(x=0.01)
    fig.autofmt_xdate()
    fig.savefig(out_path, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_path


def render_candles(df, out_path, ticker="", fmt="svg", caption=None,
                   key_points=None, hlevels=None, levels=None, max_width_px=1000, dpi=100):
    """Candlestick + volume chart — the human-review output (SVG by default).

    ``caption`` (optional) draws a text box on the price panel with the verdict.
    ``key_points`` (optional) is a list of ``{"label", "date", "price"}`` dicts the
    model returned; each is drawn as a red labelled dot at the matching bar, so the
    SVG shows *where* the detected pattern sits. ``hlevels`` (optional) is a list of
    prices drawn as dashed horizontal lines (neckline / rim). ``levels`` (optional)
    is a list of support/resistance ``Level`` objects (``.price``, ``.kind``,
    ``.strength``) drawn as green (support) / red (resistance) dotted lines whose
    width scales with strength — the same style as the rule-based charts.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    width_in = max_width_px / dpi
    height_in = width_in * 0.66

    # Build the key-point scatter aligned to df's positional x-axis.
    labels = []  # (x_pos, y_price, name)
    scatter = np.full(len(df), np.nan)
    for kp in key_points or []:
        pos = _nearest_pos(df, kp["date"])
        if pos is None:
            continue
        y = _snap_price(df.iloc[pos], kp["price"])
        scatter[pos] = y
        labels.append((pos, y, kp.get("label", "")))

    plot_kwargs = dict(
        type="candle", volume=True, style="yahoo",
        figsize=(width_in, height_in), returnfig=True, tight_layout=True,
        datetime_format="%Y-%m", title=ticker,
    )
    if np.isfinite(scatter).any():
        plot_kwargs["addplot"] = mpf.make_addplot(
            scatter, type="scatter", markersize=90, marker="o", color="#ef4444"
        )

    fig, axes = mpf.plot(df, **plot_kwargs)
    price_ax = axes[0]

    for x, price, name in labels:
        price_ax.annotate(
            name, (x, price), textcoords="offset points", xytext=(0, 10),
            ha="center", fontsize=8, color="#ef4444",
        )
    for lv in hlevels or []:
        price_ax.axhline(lv, color="#6b7280", linestyle="--", linewidth=1)

    # Support / resistance overlay (rule-based levels, not model output).
    for lv in levels or []:
        color = "#dc2626" if lv.kind == "resistance" else "#16a34a"
        price_ax.axhline(lv.price, color=color, alpha=0.35,
                         lw=0.6 + 1.4 * lv.strength, ls=":")

    if caption:
        price_ax.text(
            0.01, 0.98, caption, transform=price_ax.transAxes,
            va="top", ha="left", fontsize=8, color="#111827",
            bbox=dict(boxstyle="round", facecolor="white", edgecolor="#9ca3af", alpha=0.85),
        )
    fig.savefig(out_path, format=fmt, bbox_inches="tight")
    plt.close(fig)
    return out_path
