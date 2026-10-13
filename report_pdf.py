"""The complete daily report: a summary, then one chart page per ticker.

Built with matplotlib's PdfPages, which adds no dependency because every chart
already renders through matplotlib. Pages are A4 landscape.

The summary leads with a count per setup and the breakouts to act on, then sets
Bullish and Bearish side by side, each grouped by main category and
subcategory. Each ticker's page shows the weekly and daily charts next to each
other (alert_chart.py).
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

import alert_chart  # noqa: E402
import alert_grouping  # noqa: E402
import report_style as st  # noqa: E402
import setups  # noqa: E402
import sr_render  # noqa: E402

_COLUMNS = ["ticker", "tf", "level", "side", "event", "confl", "score", "close"]
_MAX_TABLE_ROWS = 40


def _row_cells(a):
    return [a["ticker"], a.get("timeframe") or "-",
            f"{a['price']:,.2f}" if a.get("price") else "-",
            a.get("kind") or "-", a["event"],
            "yes" if a.get("confluence") else "",
            f"{a.get('score') or 0:.2f}", f"{a.get('close') or 0:,.2f}"]


def _summary_page(pdf, alerts, run_date, warnings):
    """The flat table, used when no universe is available to group by."""
    fig = plt.figure(figsize=st.PAGE)
    fig.suptitle(f"S&P 500 monitor - {run_date}", fontsize=14, y=0.97)
    ax = fig.add_subplot(111)
    ax.axis("off")

    if alerts:
        body = [_row_cells(a) for a in alerts[:_MAX_TABLE_ROWS]]
        table = ax.table(cellText=body, colLabels=_COLUMNS, loc="upper center",
                         cellLoc="right")
        table.auto_set_font_size(False)
        table.set_fontsize(7)
        table.scale(1, 1.3)
        if len(alerts) > _MAX_TABLE_ROWS:
            ax.text(0.5, 0.02,
                    f"{len(alerts) - _MAX_TABLE_ROWS} further alerts not listed",
                    ha="center", transform=ax.transAxes, fontsize=8)
    else:
        ax.text(0.5, 0.6, "No alerts", ha="center", fontsize=12,
                transform=ax.transAxes)

    footer = f"{len(alerts)} alert(s)"
    if warnings:
        footer += "   |   " + "   |   ".join(warnings)
    fig.text(0.5, 0.04, footer, ha="center", fontsize=8,
             color=st.WARN if warnings else st.INK_2)

    pdf.savefig(fig)
    plt.close(fig)


# --- grouped summary ---------------------------------------------------------

_LINE = 0.0200                  # one table line, figure fraction
_COL_X = (0.04, 0.53)           # left edges of the two table columns
_COL_W = 0.43
_FIRST_TOP, _NEXT_TOP, _BOTTOM = 0.6, 0.86, 0.075
# Without a clusters line the table moves up into its space.
_FIRST_TOP_NO_CLUSTERS = 0.64
# A summary a few lines too long for one page is set a little tighter rather
# than spilling three rows onto a page of their own.
_MIN_LINE = 0.0165


def breakouts(alerts):
    """Urgent rows, one per ticker: each ticker's strongest setup, strongest
    tickers first (a confirmed breakout before a setup)."""
    best = {}
    for a in alerts:
        if a.get("setup") not in setups.URGENT:
            continue
        held = best.get(a["ticker"])
        if held is None or setups.rank(a["setup"]) < setups.rank(held["setup"]):
            best[a["ticker"]] = a
    order = list(best)
    return sorted(best.values(),
                  key=lambda a: (setups.rank(a["setup"]), order.index(a["ticker"])))


def summary_columns(alerts, universe_rows):
    """``(left, right)`` display items: Bullish left, everything else right.

    Items are tuples: ``("outlook", name, count)``, ``("category", name,
    count)``, ``("subcategory", name, count)``, ``("row", alert)``,
    ``("empty", text)`` and ``("gap",)``.
    """
    grouped = alert_grouping.group(alerts, universe_rows)
    left, right = [], []
    for outlook, cats in grouped.items():
        col = left if outlook == setups.BULLISH else right
        if col:
            col.append(("gap",))
        col.append(("outlook", outlook, alert_grouping.outlook_count(cats)))
        for cat, subs in cats.items():
            col.append(("category", cat, sum(len(v) for v in subs.values())))
            for sub, rows in subs.items():
                col.append(("subcategory", sub, len(rows)))
                col += [("row", r) for r in rows]
    if not left:
        left = [("outlook", setups.BULLISH, 0), ("empty", "No bullish setups today")]
    if not right:
        right = [("outlook", setups.BEARISH, 0), ("empty", "No bearish setups today")]
    return left, right


def _height(item):
    """Lines an item occupies: the outlook heading carries a rule beneath it."""
    return 1.25 if item[0] == "outlook" else 1.0


def _paginate(items, first, rest):
    """Split items into pages holding ``first`` then ``rest`` lines.

    A page never ends on a header: a category with none of its rows beneath it
    reads as an empty group.
    """
    pages, i, cap = [], 0, first
    while i < len(items):
        end, used = i, 0.0
        while end < len(items) and used + _height(items[end]) <= cap + 1e-9:
            used += _height(items[end])
            end += 1
        end = max(end, i + 1)
        while end < len(items) and end - i > 1 and items[end - 1][0] in (
                "outlook", "category", "subcategory", "gap"):
            end -= 1
        pages.append(items[i:end])
        i, cap = end, rest
    return pages or [[]]


def _text(fig, x, y, s, **kw):
    kw.setdefault("fontsize", 7.5)
    kw.setdefault("color", st.INK)
    kw.setdefault("va", "center")
    fig.text(x, y, s, **kw)


def _rect(fig, x, y, w, h, colour, rounded=False):
    if rounded:
        fig.patches.append(FancyBboxPatch(
            (x, y), w, h, transform=fig.transFigure, facecolor=colour,
            edgecolor="none", boxstyle="round,pad=0,rounding_size=0.008"))
    else:
        fig.patches.append(Rectangle((x, y), w, h, transform=fig.transFigure,
                                     facecolor=colour, edgecolor="none"))


def _rule(fig, x0, x1, y, colour=st.RULE, lw=0.7):
    fig.lines.append(plt.Line2D([x0, x1], [y, y], transform=fig.transFigure,
                                color=colour, lw=lw))


def _column_heading(fig, x, y):
    for dx, label, ha in ((0.011, "TICKER", "left"), (0.058, "SETUP", "left"),
                          (0.15, "EVENT · LEVEL", "left"),
                          (0.297, "TREND", "left"), (_COL_W - 0.042, "CLOSE", "right"),
                          (_COL_W, "CAP", "right")):
        _text(fig, x + dx, y, label, fontsize=6, color=st.MUTED, ha=ha)


def _draw_item(fig, x, y, item):
    kind = item[0]
    if kind == "outlook":
        name, n = item[1], item[2]
        glyph = st.OUTLOOK_GLYPH.get(name, "◆")
        _text(fig, x, y, f"{glyph}  {name.upper()}", fontsize=11,
              fontweight="bold")
        _text(fig, x + _COL_W, y, f"{n} alert{'s' if n != 1 else ''}",
              fontsize=8, color=st.INK_2, ha="right")
        _rule(fig, x, x + _COL_W, y - 0.014, st.INK, 0.9)
    elif kind == "category":
        _text(fig, x, y - 0.002, item[1], fontsize=8.5, fontweight="bold")
        _text(fig, x + _COL_W, y - 0.002, str(item[2]), fontsize=7.5,
              color=st.MUTED, ha="right")
    elif kind == "subcategory":
        _text(fig, x + 0.011, y, item[1], fontsize=7.5, color=st.INK_2,
              style="italic")
    elif kind == "row":
        r = item[1]
        colour = st.SETUP_COLOR.get(r.get("setup"), st.MUTED)
        _rect(fig, x + 0.011, y - 0.0065, 0.0035, 0.013, colour)
        _text(fig, x + 0.019, y, r["ticker"], fontweight="bold", fontsize=8)
        # A long-range high's event text already names it.
        name = ("Breakout" if setups.is_long_high(r)
                else setups.short_display(r) or r["event"])
        _text(fig, x + 0.058, y, name,
              color=st.INK_2)
        _text(fig, x + 0.15, y, st.level_text(r))
        _text(fig, x + 0.297, y, st.trend_pair(r), color=st.INK_2)
        _text(fig, x + _COL_W - 0.042, y, f"{r.get('close') or 0:,.2f}", ha="right")
        _text(fig, x + _COL_W, y,
              alert_grouping.format_cap(r.get("market_cap")).strip(),
              color=st.MUTED, ha="right")
    elif kind == "empty":
        _text(fig, x + 0.011, y, item[1], color=st.MUTED, style="italic")


def _tiles(fig, alerts):
    counts = {s: sum(a.get("setup") == s for a in alerts) for s in setups.ORDER}
    n, gap, top, h = len(setups.ORDER), 0.0125, 0.855, 0.11
    w = (0.92 - (n - 1) * gap) / n
    for i, s in enumerate(setups.ORDER):
        x = 0.04 + i * (w + gap)
        colour = st.SETUP_COLOR[s]
        _rect(fig, x, top - h, w, h, st.SURFACE_2, rounded=True)
        _rect(fig, x, top - h, 0.005, h, colour)
        _text(fig, x + 0.016, top - 0.032, str(counts[s]), fontsize=22,
              fontweight="bold")
        _text(fig, x + w - 0.012, top - 0.026,
              f"{st.OUTLOOK_GLYPH[setups.OUTLOOK[s]]} {setups.OUTLOOK[s]}",
              fontsize=7.5, color=st.INK_2, ha="right")
        _text(fig, x + 0.016, top - 0.07, setups.LABEL[s], fontsize=8.5,
              fontweight="bold")
        _text(fig, x + 0.016, top - 0.094, setups.DEFINITION[s], fontsize=6.5,
              color=st.INK_2)


def _monitor_now(fig, alerts):
    rows = breakouts(alerts)
    top, h = 0.728, 0.05
    colour = st.SETUP_COLOR[setups.BREAKOUT]
    _rect(fig, 0.04, top - h, 0.92, h, st.tint(colour, 0.9), rounded=True)
    _rect(fig, 0.04, top - h, 0.005, h, colour)
    _text(fig, 0.055, top - h / 2, "MONITOR NOW", fontsize=9.5,
          fontweight="bold", color=colour)
    if not rows:
        _text(fig, 0.155, top - h / 2, "No breakouts or breakout setups today.",
              fontsize=8.5, color=st.INK_2)
        return
    # Ticker, setup and price only: the band is one line across the page and
    # the full detail is in the rows below.
    parts = [f"{r['ticker']}  {setups.short_display(r).lower()} {r['price']:,.2f}"
             for r in rows]
    shown, used = [], 0
    for p in parts:
        if used + len(p) > 150:
            break
        shown.append(p)
        used += len(p) + 6
    more = f"     +{len(parts) - len(shown)} more" if len(parts) > len(shown) else ""
    _text(fig, 0.155, top - h / 2, "      ".join(shown) + more, fontsize=8.5)


def _clusters_line(fig, alerts, universe_rows, min_cluster=3):
    """The clusters line, if any; returns whether one was drawn."""
    found = alert_grouping.clusters(
        alert_grouping.group(alerts, universe_rows), min_cluster)
    if not found:
        return False
    text = "    ".join(f"{c['subcategory']} ({', '.join(c['tickers'][:5])})"
                       for c in found[:4])
    _text(fig, 0.04, 0.652, "Clusters", fontsize=8, fontweight="bold")
    _text(fig, 0.095, 0.652, text, fontsize=8, color=st.INK_2)
    return True


def _layout(left, right, has_clusters):
    """``(first_top, line)``: where page 1's table starts, and line height."""
    top = _FIRST_TOP if has_clusters else _FIRST_TOP_NO_CLUSTERS
    needed = max(sum(_height(i) for i in col) for col in (left, right)) + 1
    line = _LINE
    if needed * _LINE > top - _BOTTOM >= needed * _MIN_LINE:
        line = (top - _BOTTOM) / needed
    return top, line


def _header(fig, run_date, alerts, scanned, page, pages):
    _text(fig, 0.04, 0.94, "Setup monitor", fontsize=20, fontweight="bold")
    shown = f"{len(alerts)} setup{'s' if len(alerts) != 1 else ''}"
    scan = f"{scanned} level events scanned  →  " if scanned is not None else ""
    _text(fig, 0.04, 0.9, f"US session {run_date}   ·   S&P 500 + AI chain"
                          f"   ·   {scan}{shown}",
          fontsize=9, color=st.INK_2)
    _text(fig, 0.96, 0.94, f"{page} / {pages}", fontsize=8, color=st.MUTED,
          ha="right")


def _footer(fig, warnings):
    _rule(fig, 0.04, 0.96, 0.05)
    legend = ("Trend  ↑ up   ↓ down   ↔ sideways   ? undecided"
              "      D daily   W weekly      S support   R resistance"
              "      ★ daily and weekly levels coincide")
    _text(fig, 0.04, 0.032, legend, fontsize=6.5, color=st.MUTED)
    if warnings:
        _text(fig, 0.96, 0.032, "⚠  " + "   |   ".join(warnings),
              fontsize=7, color=st.WARN, ha="right")


def _summary_pages(pdf, alerts, universe_rows, run_date, warnings, scanned):
    left, right = summary_columns(alerts, universe_rows)
    has_clusters = bool(alert_grouping.clusters(
        alert_grouping.group(alerts, universe_rows), 3))
    first_top, line = _layout(left, right, has_clusters)
    first = (first_top - _BOTTOM) / line - 1
    rest = (_NEXT_TOP - _BOTTOM) / line - 1
    lp, rp = _paginate(left, first, rest), _paginate(right, first, rest)
    n = max(len(lp), len(rp))

    for i in range(n):
        fig = plt.figure(figsize=st.PAGE, facecolor="white")
        _header(fig, run_date, alerts, scanned, i + 1, n)
        if i == 0:
            _tiles(fig, alerts)
            _monitor_now(fig, alerts)
            _clusters_line(fig, alerts, universe_rows)
        top = first_top if i == 0 else _NEXT_TOP
        for x, pages in zip(_COL_X, (lp, rp)):
            items = pages[i] if i < len(pages) else []
            if not items:
                continue
            _column_heading(fig, x, top + 0.006)
            y = top - line
            for item in items:
                _draw_item(fig, x, y, item)
                y -= line * _height(item)
        _footer(fig, warnings)
        pdf.savefig(fig)
        plt.close(fig)


# --- the report --------------------------------------------------------------

def build(out_path, alerts, charts, run_date, warnings=None, universe_rows=None,
          scanned=None):
    """Write the report. ``charts`` maps ticker -> check_alerts.chart_for().

    With ``universe_rows`` the summary is the designed, grouped layout; without
    it, the original flat table is used. ``scanned`` is how many level events
    the run tested, shown in the header.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_ticker = {r["ticker"]: r for r in universe_rows or []}

    with PdfPages(out_path) as pdf:
        if universe_rows:
            _summary_pages(pdf, alerts, universe_rows, run_date, warnings,
                           scanned)
        else:
            _summary_page(pdf, alerts, run_date, warnings)
        order = chart_order(alerts, charts, universe_rows)
        for i, (divider, label, ticker) in enumerate(order):
            if divider is not None:
                names = []
                for d, _, t in order[i + 1:]:
                    if d is not None:
                        break
                    names.append(t)
                _divider_page(pdf, divider, names)
            else:
                _chart_page(pdf, ticker, charts[ticker], label, run_date,
                            out_path.parent,
                            [a for a in alerts if a["ticker"] == ticker],
                            by_ticker.get(ticker))
    return out_path


def chart_order(alerts, charts, universe_rows):
    """Chart pages in grouped order, with divider markers between categories.

    Returns ``(divider, label, ticker)`` triples. A non-None ``divider`` is the
    title of a divider page (outlook and main category); otherwise ``ticker``
    is a chart and ``label`` names its group.

    Without a universe the order falls back to alphabetical, which is what the
    report did before grouping existed.
    """
    if not universe_rows:
        return [(None, None, t) for t in sorted(charts)]

    grouped = alert_grouping.group(alerts, universe_rows)
    out, seen, current = [], set(), None
    for outlook, cat, sub, r in alert_grouping.iter_rows(grouped):
        t = r["ticker"]
        if t in seen or t not in charts:
            continue
        seen.add(t)
        title = f"{outlook} - {cat}"
        if title != current:
            out.append((title, None, None))
            current = title
        out.append((None, f"{outlook}  ·  {cat}  ›  {sub}", t))

    # A chart with no alert row still gets a page rather than vanishing.
    for t in sorted(set(charts) - seen):
        out.append((None, "Unclassified", t))
    return out


def _divider_page(pdf, title, names):
    """A title page announcing each category's block of charts."""
    outlook, _, cat = title.partition(" - ")
    fig = plt.figure(figsize=st.PAGE, facecolor="white")
    glyph = st.OUTLOOK_GLYPH.get(outlook, "◆")
    _text(fig, 0.08, 0.6, f"{glyph}  {outlook.upper()}", fontsize=13,
          color=st.INK_2, fontweight="bold")
    _text(fig, 0.08, 0.52, cat or title, fontsize=28, fontweight="bold")
    _rule(fig, 0.08, 0.92, 0.47)
    _text(fig, 0.08, 0.43, f"{len(names)} chart{'s' if len(names) != 1 else ''}"
                           f"   ·   " + ", ".join(names[:24])
                           + (" ..." if len(names) > 24 else ""),
          fontsize=10, color=st.INK_2)
    pdf.savefig(fig)
    plt.close(fig)


def _chart_page(pdf, ticker, chart, label, run_date, tmp_dir, alerts=(),
                meta=None):
    if isinstance(chart, dict):
        fig = alert_chart.figure(ticker, chart, list(alerts), meta, label)
        pdf.savefig(fig, dpi=300)
        plt.close(fig)
        return

    # A bare (df, levels, extras) chart: the single-panel layout.
    df, levels, extras = chart
    png = tmp_dir / f"_page_{ticker}.png"
    title = f"{ticker} - {run_date}" + (f"   [{label}]" if label else "")
    sr_render.render_levels(df, levels, png, ticker=ticker, extras=extras,
                            title=title, fmt="png")
    fig = plt.figure(figsize=st.PAGE)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.imshow(plt.imread(png))
    pdf.savefig(fig)
    plt.close(fig)
    png.unlink(missing_ok=True)


def page_count(path) -> int:
    """Pages in a written PDF, counted without a PDF library."""
    data = Path(path).read_bytes()
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")
