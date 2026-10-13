"""Telegram Bot API client. The only outbound channel in the monitor.

Credentials resolve from the environment (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID),
the same pattern the Gemini client uses. They belong in .env; nothing here reads
that file.
"""
import html
import os

import requests

import setups

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_MESSAGE = 4096          # Telegram's hard limit, in UTF-8 bytes

# A watch is a price the user named themselves, so it outranks anything the
# model found. The rest is the automatic tiering.
_RANK = {"watch": 0, "confluence": 1, "shock": 2, "break": 3, "touch": 4}


def credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set; "
            "add them to .env and source it")
    return token, chat


def is_watch(row) -> bool:
    """A user-named price alert rather than a level the model found."""
    return row.get("timeframe") == "watch"


def _rank(row):
    if is_watch(row):
        return _RANK["watch"]
    if row.get("confluence"):
        return _RANK["confluence"]
    if row["event"] in ("jump", "drop"):
        return _RANK["shock"]
    return _RANK.get(row["event"], 9)


def trend_tag(row) -> str:
    """``D sideways / W up``, or empty when the trends were not recorded."""
    d, w = row.get("daily_trend"), row.get("weekly_trend")
    if not d and not w:
        return ""
    return f"D {d or '?'} / W {w or '?'}"


def setup_label(row) -> str:
    return setups.display(row)


def format_alert(row) -> str:
    """One line for one alert.

    A watch leads with its own title: the user chose the price and the words,
    and the point of naming a watch is that the alert arrives labelled. A setup
    leads with its name and ends with the trends that qualified it.
    """
    if is_watch(row):
        title = row.get("title") or "Price watch"
        return (f"{title}\n{row['ticker']} {row['event']} {row['kind']} "
                f"{row['price']:,.2f} - close {row['close']:,.2f}")

    if row["event"] in ("jump", "drop"):
        pct = (row.get("score") or 0) * 100
        return (f"{row['event'].upper()} {row['ticker']} "
                f"{pct:+.1f}% to {row['close']:,.2f}")

    tag ="CONFLUENCE " if row.get("confluence") else ""
    head = f"{setup_label(row).upper()}\n" if row.get("setup") else ""
    tail = f"\n{trend_tag(row)}" if trend_tag(row) else ""
    return (f"{head}{tag}{row['ticker']} {row['event']} {row['kind']} "
            f"{row['price']:,.2f} ({row['timeframe']}) close "
            f"{row['close']:,.2f}{tail}")


# --- setup messages (Telegram HTML) -------------------------------------------
#
# Setup messages are sent with parse_mode=HTML: bold headings and one coloured
# dot per setup, the same hues as the PDF, read far faster on a phone than
# bracketed tags in plain text. Every name that reaches them is escaped.

DOT = {setups.CONFIRMED: "🚀", setups.BREAKOUT: "🟣", setups.BASING: "🔵",
       setups.PULLBACK: "🟢", setups.MAJOR_SUPPORT: "🟠"}
_ARROW = {"up": "↑", "down": "↓", "sideways": "↔", "undecided": "?"}
OUTLOOK_MARK = {setups.BULLISH: "▲", setups.SIDEWAYS: "↔", setups.BEARISH: "▼",
                "Price watches": "📌"}
_RULE = "━━━━━━━━━━━━━━━━"
_LEGEND = ("🚀 Breakout  🟣 BO setup  🔵 Reversal base  🟢 Pullback  "
           "🟠 Support test\n★ daily and weekly levels coincide  ·  "
           "D/W = daily/weekly trend  ·  charts in the PDF")


def _esc(text):
    return html.escape(str(text), quote=False)


def _arrows(row):
    """``D ↔  W ↑``."""
    return (f"D {_ARROW.get(row.get('daily_trend'), '–')}  "
            f"W {_ARROW.get(row.get('weekly_trend'), '–')}")


def _trend_words(row):
    """``D ↔ sideways, W ↑ up``."""
    d, w = row.get("daily_trend") or "?", row.get("weekly_trend") or "?"
    return f"D {_ARROW.get(d, '?')} {d}, W {_ARROW.get(w, '?')} {w}"


def level_phrase(row):
    """What price did, briefly: ``broke major R 106.00 (W) ★``."""
    if setups.is_long_high(row):
        return f"closed above {setups.LONG_HIGH_SHORT} {row['price']:,.2f}"
    if row.get("setup") == setups.CONFIRMED:
        tf = {"weekly": "W", "daily": "D"}.get(row.get("timeframe"), "")
        since = f", setup {row['setup_ts']}" if row.get("setup_ts") else ""
        return f"closed through R {row['price']:,.2f} ({tf}){since}"
    verb = "broke" if row["event"] == "break" else "at"
    side = "S" if row.get("kind") == "support" else "R"
    tf = {"weekly": "W", "daily": "D"}.get(row.get("timeframe"), "")
    major = "major " if row.get("is_major") else ""
    star = " ★" if row.get("confluence") else ""
    return f"{verb} {major}{side} {row['price']:,.2f} ({tf}){star}"


def by_ticker(rows):
    """``[(ticker, rows)]`` in first-seen order: one entry per name, so a
    ticker at two levels reads as one name, not two alerts. Each name's rows
    lead with its strongest setup, which is the one its entry is named for."""
    out = {}
    for r in rows:
        out.setdefault(r["ticker"], []).append(r)
    return [(t, sorted(rs, key=lambda r: setups.rank(r.get("setup"))))
            for t, rs in out.items()]


def _cap(row):
    import alert_grouping as ag
    return ag.format_cap(row.get("market_cap")).strip()


def _entry(ticker, rows):
    """Two lines for one name in the digest."""
    r = rows[0]
    if is_watch(r):
        return (f"📌 <b>{_esc(ticker)}</b>  {_esc(r.get('title') or 'Price watch')}"
                f"\n      {r['event']} {r['price']:,.2f} · close {r['close']:,.2f}")
    head = (f"{DOT.get(r.get('setup'), '•')} <b>{_esc(ticker)}</b>  "
            f"{_esc(setups.display(r))}   {_arrows(r)}")
    detail = [level_phrase(x) for x in rows if x.get("price") is not None]
    detail += [x["note"] for x in rows if x.get("note")]
    detail.append(f"close {r.get('close') or 0:,.2f}")
    if _cap(r):
        detail.append(_cap(r))
    return head + "\n      " + _esc(" · ".join(detail))


def format_push(rows, universe_rows=None):
    """One urgent name, every level it fired at, as a self-contained push."""
    import alert_grouping as ag

    rows = sorted(rows, key=lambda x: setups.rank(x.get("setup")))
    r = ag.classify_alerts(rows[:1], universe_rows or [])[0]
    meta = next((u for u in universe_rows or [] if u["ticker"] == r["ticker"]), {})
    who = [meta.get("name"), r["subcategory"], _cap(r)]
    lines = [f"{DOT.get(r.get('setup'), '•')} <b>{_esc(setups.display(r).upper())}"
             f" · {_esc(r['ticker'])}</b>",
             _esc(" · ".join(w for w in who if w and w != "Other"))]
    if setups.is_long_high(r):
        pct = (r["close"] / r["price"] - 1) * 100
        lines.append(f"Closed {r['close']:,.2f} above its weekly "
                     f"{setups.LONG_HIGH_NAME} "
                     f"{r['price']:,.2f} ({pct:+.1f}%)")
    else:
        lines += [_esc(level_phrase(x)) for x in rows]
        lines += [_esc(x["note"][:1].upper() + x["note"][1:])
                  for x in rows if x.get("note")]
        lines.append(f"Close {r['close']:,.2f}")
    lines.append(f"Trend {_trend_words(r)}")
    return "\n".join(lines)


def format_setup_digest(rows, universe_rows, session, limit=40,
                        min_cluster=3) -> str:
    """The evening digest: what to watch first, then every name by outlook,
    main category and subcategory, two lines per name.
    """
    import alert_grouping as ag

    if not rows:
        return f"<b>📊 Setup monitor · session {session}</b>\nNo setups today."

    grouped = ag.group(rows, universe_rows)
    names = len({r["ticker"] for r in rows})
    counts = "  ·  ".join(
        f"{OUTLOOK_MARK.get(o, '')} {ag.outlook_count(c)} {o.lower()}".strip()
        for o, c in grouped.items())
    head = [f"<b>📊 Setup monitor · session {session}</b>",
            f"{len(rows)} setups across {names} stocks  ·  {counts}"]

    urgent = sorted(by_ticker([r for *_, r in ag.iter_rows(grouped)
                               if r.get("setup") in setups.URGENT]),
                    key=lambda tr: setups.rank(tr[1][0].get("setup")))
    if urgent:
        head += ["", "<b>🔔 MONITOR NOW</b>"]
        head += [f"{DOT[rs[0]['setup']]} <b>{_esc(t)}</b>  "
                 f"{_esc(level_phrase(rs[0]))}" for t, rs in urgent[:10]]
    found = ag.clusters(grouped, min_cluster)
    if found:
        head += ["", "<b>🧩 Clusters</b>"]
        head += [f"• {_esc(c['subcategory'])}: {_esc(', '.join(c['tickers'][:6]))}"
                 for c in found[:4]]

    def build(n):
        out, shown, total = list(head), 0, 0
        for outlook, cats in grouped.items():
            out += ["", _RULE, f"<b>{OUTLOOK_MARK.get(outlook, '')} "
                               f"{_esc(outlook.upper())} · {ag.outlook_count(cats)}</b>"]
            for cat, subs in cats.items():
                out += ["", f"<b>{_esc(cat)}</b>"]
                for sub, sub_rows in subs.items():
                    out.append(f"<i>{_esc(sub)}</i>")
                    for t, rs in by_ticker(sub_rows):
                        total += 1
                        if shown < n:
                            out.append(_entry(t, rs))
                            shown += 1
        if total > shown:
            out += ["", f"…and {total - shown} more names in the PDF."]
        out += ["", _RULE, _LEGEND]
        return "\n".join(out)

    body = build(limit)
    while len(body.encode("utf-8")) > _MAX_MESSAGE and limit > 5:
        limit -= 5
        body = build(limit)
    return body


def deliver_setups(rows, universe_rows, session, pdf_path, max_push,
                   digest_limit=40):
    """Send one evening's setups: urgent names pushed one by one (capped), then
    the digest, then the PDF. Returns ``(pushed, urgent)`` name counts.

    Pushes are per NAME: a stock at its daily and weekly resistance at once is
    one thing to look at, not two buzzes.
    """
    urgent = sorted(by_ticker(sorted(
        (r for r in rows if r.get("setup") in setups.URGENT),
        key=lambda r: (-(r.get("confluence") or 0), -(r.get("score") or 0)))),
        key=lambda tr: setups.rank(tr[1][0].get("setup")))
    pushed = urgent[:max_push]
    for _, rs in pushed:
        send_message(format_push(rs, universe_rows), parse_mode="HTML")
    if len(urgent) > len(pushed):
        rest = ", ".join(t for t, _ in urgent[len(pushed):])
        send_message(f"…and {len(urgent) - len(pushed)} more to monitor: "
                     f"{rest}. See the digest below.")
    send_message(format_setup_digest(rows, universe_rows, session, digest_limit),
                 parse_mode="HTML")
    send_document(pdf_path, caption=f"Setup monitor · session {session}")
    return len(pushed), len(urgent)


def format_digest(rows, limit=20) -> str:
    """The evening message: ranked, capped, and honest about what it omitted."""
    if not rows:
        return "No alerts today."

    ordered = sorted(rows, key=lambda r: (_rank(r), -(r.get("score") or 0)))
    shown = ordered[:limit]

    def build(subset):
        lines = [f"{len(rows)} alert(s) today", ""]
        lines += [format_alert(r) for r in subset]
        if len(ordered) > len(subset):
            lines += ["", f"...and {len(ordered) - len(subset)} more "
                          "in the attached report."]
        return "\n".join(lines)

    body = build(shown)
    # Trim until it fits rather than letting Telegram reject the whole message.
    while len(body.encode("utf-8")) > _MAX_MESSAGE and len(shown) > 1:
        shown = shown[:-1]
        body = build(shown)
    return body


def _check(resp, method):
    """Raise on failure WITHOUT leaking the token.

    The bot token sits in the request path, so requests' own
    ``raise_for_status`` puts it in the exception message — and therefore into
    logs/check_alerts.log on every failed send once cron is running. Telegram's
    own JSON error description is the useful part; the URL never is.
    """
    if resp.ok:
        return resp.json()
    try:
        detail = resp.json().get("description", "")
    except ValueError:
        detail = resp.text[:200]
    raise RuntimeError(f"Telegram {method} failed: {resp.status_code} {detail}")


def send_message(text, token=None, chat=None, timeout=30, parse_mode=None):
    token, chat = (token, chat) if token and chat else credentials()
    data = {"chat_id": chat, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    resp = requests.post(_API.format(token=token, method="sendMessage"),
                         data=data, timeout=timeout)
    return _check(resp, "sendMessage")


def send_document(path, caption=None, token=None, chat=None, timeout=120):
    token, chat = (token, chat) if token and chat else credentials()
    with open(path, "rb") as fh:
        resp = requests.post(
            _API.format(token=token, method="sendDocument"),
            data={"chat_id": chat, "caption": caption or ""},
            files={"document": fh}, timeout=timeout)
    return _check(resp, "sendDocument")
