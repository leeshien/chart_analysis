"""One alerted ticker on one page: the weekly chart and the daily chart side by side.

A setup is a statement about two timeframes at once - "daily sideways, weekly
up" - so the page puts both charts next to each other, each with its own levels
and its own trend verdict, measured on the alert day. The alert itself is
marked on both: the bar it happened on and the level it happened at.

Candles are rasterized and everything else stays vector, so text is crisp at
any zoom while a page of 400 candles stays around 150KB.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

import alert_grouping  # noqa: E402
import bars as bars_mod  # noqa: E402
import report_style as st  # noqa: E402
import setup_context  # noqa: E402
import setups  # noqa: E402
import trend as trend_mod  # noqa: E402

WEEKLY_BARS = 156          # three years
DAILY_BARS = 252           # one year

_STYLE = mpf.make_mpf_style(
    base_mpf_style="default",
    marketcolors=mpf.make_marketcolors(up=st.CANDLE_UP, down=st.CANDLE_DOWN,
                                       edge="inherit", wick="inherit",
                                       volume=st.VOLUME),
    facecolor=st.SURFACE, edgecolor=st.RULE, gridcolor="#ecebe7",
    gridstyle="-", y_on_right=False,
    rc={"font.size": 7, "axes.labelcolor": st.INK_2,
        "xtick.color": st.INK_2, "ytick.color": st.INK_2,
        "axes.linewidth": 0.6})

_LABEL_GAP = 0.042         # minimum spacing of level labels, axes fraction


def frames(bars_df):
    """``(weekly, daily)`` display frames ending at the newest bar.

    The weekly frame keeps the current, unfinished week so the newest price
    action is visible; the trend verdict is computed separately on completed
    weeks only.
    """
    weekly = bars_mod.derive_weekly(bars_df, drop_partial=False)
    return weekly.tail(WEEKLY_BARS), bars_df.tail(DAILY_BARS)


def _bar_of(df, day, weekly):
    """Integer position of the bar holding ``day`` in ``df``, or None."""
    if weekly:
        for i, end in enumerate(df.index.date):
            if end.isoformat() >= day:
                return i
        return None
    dates = [d.isoformat() for d in df.index.date]
    return dates.index(day) if day in dates else None


def _draw_levels(ax, levels, close, alert_kinds, alert_names=None):
    """Level lines across the panel, labels de-overlapped on the right edge.

    ``alert_kinds`` maps each alerted price to the role it had when it fired,
    so a broken resistance is still labelled R even once price is above it.
    ``alert_names`` overrides the label of an alerted price that is not a
    stored level, such as a long-range high.

    The alerted label is kept short: it sits in the gutter between the two
    panels, and the purple line and arrow already say which level fired.
    """
    alert_names = alert_names or {}
    lo, hi = ax.get_ylim()
    items = []
    for lv in levels:
        p = float(lv["price"])
        if not lo <= p <= hi:
            continue
        support = p < close
        major = bool(lv.get("is_major"))
        fired = next((k for a, k in alert_kinds.items()
                      if abs(p - a) <= 1e-6 * max(1.0, a)), None)
        alert = fired is not None
        colour = st.ALERT if alert else (st.SUPPORT if support else st.RESISTANCE)
        ax.axhline(p, color=colour, zorder=3,
                   lw=2.2 if alert else (1.3 if major else 0.8),
                   ls="-" if (major or alert) else (0, (4, 3)),
                   alpha=1.0 if alert else (0.9 if major else 0.6))
        if alert:
            support = fired == "support"
        tag = "S" if support else "R"
        text = (f"MAJOR {tag}  " if major else f"{tag.lower()}  ") + f"{p:,.2f}"
        if alert:
            name = next((n for a, n in alert_names.items()
                         if abs(p - a) <= 1e-6 * max(1.0, a)), None)
            text = "◀ " + (f"{name} {p:,.2f}" if name else text.upper())
        items.append([(p - lo) / (hi - lo), text, colour, major or alert])

    items.sort(key=lambda it: it[0])
    ys = [it[0] for it in items]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + _LABEL_GAP)
    if ys and ys[-1] > 0.99:
        ys[-1] = 0.99
        for i in range(len(ys) - 2, -1, -1):
            ys[i] = min(ys[i], ys[i + 1] - _LABEL_GAP)
    for (y0, text, colour, bold), y in zip(items, ys):
        ax.annotate(text, xy=(1.0, y0), xycoords="axes fraction",
                    xytext=(1.012, y), textcoords="axes fraction",
                    fontsize=7, color=colour, va="center", ha="left",
                    fontweight="bold" if bold else "normal",
                    arrowprops=(dict(arrowstyle="-", color=colour, lw=0.5,
                                     shrinkA=0, shrinkB=0)
                                if abs(y - y0) > 0.004 else None))


def _trend_box(ax, tf, verdict, as_of):
    """Top-left verdict: the decision in words, then the numbers behind it."""
    if verdict is None:
        return
    ax.add_patch(FancyBboxPatch((0.012, 0.845), 0.52, 0.135,
                                boxstyle="round,pad=0.004,rounding_size=0.01",
                                transform=ax.transAxes, facecolor="white",
                                edgecolor=st.RULE, lw=0.6, alpha=0.94, zorder=8))
    glyph = st.TREND_GLYPH.get(verdict.state, "?")
    word = st.TREND_WORD.get(verdict.state, verdict.state)
    ax.text(0.025, 0.952, f"{tf.upper()} TREND   {glyph} {word}",
            transform=ax.transAxes, fontsize=9, fontweight="bold",
            color=st.INK, va="center", zorder=9)
    band = trend_mod.flat_drift_for(tf)
    ax.text(0.025, 0.885,
            f"drift {verdict.drift:+.2f}   (sideways inside ±{band:.2f})\n"
            f"fit R² {verdict.fit:.2f}   window {verdict.months:.0f} mo"
            f"   as of {as_of}",
            transform=ax.transAxes, fontsize=7, color=st.INK_2, va="center",
            linespacing=1.4, zorder=9)


def _mark_alerts(ax, df, alerts, weekly):
    """A faint guide and an arrow under each alert's bar."""
    lo, hi = ax.get_ylim()
    for a in alerts:
        pos = _bar_of(df, a["ts"], weekly)
        if pos is None:
            continue
        low = float(df["Low"].iloc[pos])
        ax.axvline(pos, color=st.ALERT, lw=0.8, ls=":", alpha=0.7, zorder=2)
        ax.annotate("", xy=(pos, low - 0.01 * (hi - lo)),
                    xytext=(pos, low - 0.08 * (hi - lo)),
                    arrowprops=dict(arrowstyle="-|>", color=st.ALERT, lw=1.6),
                    zorder=7)


def _panel(fig, spec, df, tf, levels, verdict, alerts, as_of):
    ax = fig.add_subplot(spec[0])
    vax = fig.add_subplot(spec[1], sharex=ax)
    mpf.plot(df, type="candle", ax=ax, volume=vax, style=_STYLE,
             datetime_format="%b %y", xrotation=0, warn_too_much_data=10_000)
    # Volume is context, not signal: one quiet grey whatever the candle did.
    for art in list(vax.collections) + list(vax.patches):
        art.set_facecolor(st.VOLUME)
        art.set_edgecolor(st.VOLUME)
    for art in list(ax.collections) + list(vax.collections) + list(vax.patches):
        art.set_rasterized(True)

    tf_alerts = [a for a in alerts
                 if a.get("price") is not None and a.get("timeframe") == tf]
    alert_prices = [float(a["price"]) for a in tf_alerts]
    # The alerted level is drawn even when it is no longer stored: a level
    # refresh between the alert and the report replaces the level set.
    levels = list(levels) + [
        {"price": a["price"], "kind": a.get("kind"), "is_major": a.get("is_major")}
        for a in tf_alerts
        if not any(abs(float(lv["price"]) - float(a["price"])) <= 1e-6 * a["price"]
                   for lv in levels)]
    lo, hi = ax.get_ylim()
    if alert_prices:
        span = hi - lo
        lo = min(lo, min(alert_prices) - 0.03 * span)
        hi = max(hi, max(alert_prices) + 0.03 * span)
    ax.set_ylim(lo - 0.06 * (hi - lo), hi + 0.16 * (hi - lo))   # room for box

    _draw_levels(ax, levels, float(df["Close"].iloc[-1]),
                 {float(a["price"]): a.get("kind") for a in tf_alerts},
                 {float(a["price"]): setups.LONG_HIGH_SHORT.upper()
                  for a in tf_alerts
                  if setups.is_long_high(a)})
    _mark_alerts(ax, df, alerts, weekly=(tf == "weekly"))
    _trend_box(ax, tf, verdict, as_of)

    span = "3 years of weekly bars" if tf == "weekly" else "12 months of daily bars"
    ax.set_title(f"{tf.capitalize()}  ·  {span}", loc="left", fontsize=9,
                 color=st.INK_2, pad=6)
    ax.set_ylabel("")
    vax.set_ylabel("")
    vax.set_yticks([])
    ax.tick_params(labelbottom=False)
    return ax


def _chip(fig, x_right, y, row):
    """A setup chip, right-aligned at ``x_right``: tinted fill, colour bar, name."""
    colour = st.SETUP_COLOR.get(row.get("setup"), st.MUTED)
    label = setups.display(row)
    width = 0.012 + 0.0068 * len(label)
    x = x_right - width
    fig.patches.append(FancyBboxPatch(
        (x, y - 0.017), width, 0.034, transform=fig.transFigure,
        boxstyle="round,pad=0,rounding_size=0.006", facecolor=st.tint(colour),
        edgecolor="none"))
    fig.patches.append(FancyBboxPatch(
        (x, y - 0.017), 0.004, 0.034, transform=fig.transFigure,
        boxstyle="square,pad=0", facecolor=colour, edgecolor="none"))
    fig.text(x + 0.009, y, label, fontsize=9, fontweight="bold", color=st.INK,
             va="center")
    return x


def _alert_sentence(a, bars=None):
    tf = {"weekly": "weekly", "daily": "daily"}.get(a.get("timeframe"), "")
    major = "major " if a.get("is_major") else ""
    verb = {"touch": "touched", "break": "broke"}.get(a["event"], a["event"])
    vol = ""
    if bars is not None:
        dates = [d.isoformat() for d in bars.index.date]
        if a["ts"] in dates:
            vol = "   ·   " + setup_context.format_volume(
                *setup_context.volume_on(bars, dates.index(a["ts"])))
    if setups.is_long_high(a):
        return (f"{a['ts']}   closed above its weekly {setups.LONG_HIGH_NAME} "
                f"{a['price']:,.2f}   ·   close {a.get('close') or 0:,.2f}{vol}")
    if a.get("setup") == setups.CONFIRMED:
        since = (f" (Breakout setup {a['setup_ts']})" if a.get("setup_ts")
                 else "")
        return (f"{a['ts']}   closed through {tf} resistance {a['price']:,.2f}"
                f"{since}   ·   close {a.get('close') or 0:,.2f}{vol}")
    conf = "  ★ daily and weekly levels coincide" if a.get("confluence") else ""
    return (f"{a['ts']}   {verb} {tf} {major}{a.get('kind') or ''} "
            f"{a['price']:,.2f}   ·   close {a.get('close') or 0:,.2f}{vol}{conf}")


def figure(ticker, chart, alerts, meta=None, group_label=None):
    """The chart page for one ticker, as a matplotlib Figure.

    ``chart`` is check_alerts.chart_for()'s dict; ``meta`` the ticker's
    universe row (name, market cap); ``alerts`` this ticker's shown alerts.
    """
    meta = meta or {}
    level_alerts = [a for a in alerts if a.get("price") is not None]
    weekly_df, daily_df = frames(chart["bars"])

    fig = plt.figure(figsize=st.PAGE, facecolor="white")

    # Header: ticker and name, group, what fired; setup chips on the right.
    fig.text(0.04, 0.945, ticker, fontsize=22, fontweight="bold", color=st.INK,
             va="center")
    sub = [meta.get("name") or ""]
    if group_label:
        sub.append(group_label)
    if meta.get("market_cap"):
        sub.append(f"mkt cap {alert_grouping.format_cap(meta['market_cap']).strip()}")
    fig.text(0.04, 0.905, "   ·   ".join(s for s in sub if s), fontsize=9.5,
             color=st.INK_2, va="center")
    # Up to two lines: the first alert, then any note it carries (a level the
    # outlook was decided against), then further alerts.
    lines = []
    for a in level_alerts:
        lines.append((_alert_sentence(a, chart["bars"]), st.INK))
        if a.get("note"):
            lines.append((f"{a['ts']}   {a['note']} (weekly trend decided the "
                          "outlook)", st.INK_2))
    for i, (text, colour) in enumerate(lines[:2]):
        fig.text(0.04, 0.872 - i * 0.026, text, fontsize=8.5, color=colour,
                 va="center")
    x = 0.96
    chips = {(a["setup"], a.get("pattern")): a for a in alerts if a.get("setup")}
    for key in sorted(chips, key=lambda k: setups.rank(k[0]), reverse=True):
        x = _chip(fig, x, 0.945, chips[key]) - 0.008
    fig.lines.append(plt.Line2D([0.04, 0.96], [0.835, 0.835],
                                transform=fig.transFigure, color=st.RULE, lw=0.8))

    gs = GridSpec(2, 2, figure=fig, left=0.05, right=0.905, top=0.775,
                  bottom=0.09, wspace=0.34, hspace=0.04, height_ratios=(5, 1))
    trends = chart.get("trends") or {}
    _panel(fig, (gs[0, 0], gs[1, 0]), weekly_df, "weekly",
           chart.get("weekly_levels") or [], trends.get("weekly"),
           alerts, chart["as_of"])
    _panel(fig, (gs[0, 1], gs[1, 1]), daily_df, "daily",
           chart.get("daily_levels") or [], trends.get("daily"),
           alerts, chart["as_of"])

    fig.text(0.04, 0.025,
             "Lines: blue support, orange resistance, solid = major, dashed = "
             "minor, purple ◀ = the level that alerted.   Trend: ↑ up  "
             "↓ down  ↔ sideways  ? undecided.",
             fontsize=7, color=st.MUTED)
    return fig
