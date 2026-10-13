"""Group alerts by outlook, then main category, then subcategory, then market cap.

A flat ranked list answers "what is most urgent". This answers a different and
often more useful question: is this one name moving, or is a whole part of the
supply chain moving at once? Three names from the same subcategory touching
support on the same day is a rotation; one is a setup.

- **Outlook** comes first: bullish setups (breakout, bottom forming) are what
  this project buys, so they lead; bearish ones (major support under test)
  follow. See setups.py.
- **Main category** is the AI supply-chain layer for chain names and the GICS
  sector for everything else. Technology and AI lead: the chain layers in
  supply-chain order, then non-chain Information Technology and Communication
  Services, then every other sector by how many alerts it carries.
- **Subcategory** is the chain sub-tier, or the GICS sub-industry outside the
  chain.

Sorting by market cap within a subcategory puts the name that actually moves an
index first, rather than whichever ticker happens to sort alphabetically.
"""
from collections import OrderedDict

import setups

_UNKNOWN_SECTOR = "Unclassified"
_OTHER_SUB = "Other"
_WATCHES = "Price watches"
_NO_SETUP = "Other alerts"

_OUTLOOK_ORDER = (setups.BULLISH, setups.SIDEWAYS, setups.BEARISH, _WATCHES,
                  _NO_SETUP)

# Chain layers keyed by the sub-tier's leading digit, in supply-chain order.
CHAIN_LAYERS = OrderedDict([
    ("1", "AI - Chip design & IP"),
    ("2", "AI - Semiconductor equipment"),
    ("3", "AI - Semiconductors"),
    ("4", "AI - Foundry & memory"),
    ("5", "AI - Networking & optical"),
    ("6", "AI - Power & cooling equipment"),
    ("7", "AI - Power generation & fuel"),
    ("8", "AI - Data-centre build-out"),
    ("9", "AI - Cloud & software"),
])

# Non-chain sectors that still count as technology, placed straight after the
# chain layers.
_TECH_SECTORS = ("Information Technology", "Communication Services")

TIER_NAMES = {
    "1a_eda_ip": "EDA & IP",
    "2a_litho": "Lithography",
    "2b_deposition_etch": "Deposition & etch",
    "2c_metrology_inspection": "Metrology & inspection",
    "2d_test": "Test",
    "2e_packaging": "Packaging",
    "2f_materials": "Materials",
    "3a_datacentre_compute": "Data-centre compute",
    "3b_custom_asic_interconnect": "Custom ASIC & interconnect",
    "3c_server_power": "Server power",
    "3d_analog_auto": "Analog & auto",
    "3e_wireless_rf": "Wireless & RF",
    "4a_foundry": "Foundry",
    "4b_memory": "Memory",
    "4c_nand_hdd": "NAND & HDD",
    "5a_switching": "Switching",
    "5b_optical_transport": "Optical transport",
    "5c_optical_components": "Optical components",
    "5d_fibre_passive": "Fibre & passive",
    "5e_connectors_copper": "Connectors & copper",
    "6a_power_distribution": "Power distribution",
    "6b_automation": "Automation",
    "6c_cooling_thermal": "Cooling & thermal",
    "6d_generation_equipment": "Generation equipment",
    "6e_grid_epc": "Grid EPC",
    "7a_merchant_power": "Merchant power",
    "7b_nuclear_fuel": "Nuclear & fuel",
    "7c_regulated_utilities": "Regulated utilities",
    "8a_servers_storage": "Servers & storage",
    "8b_contract_manufacture": "Contract manufacturing",
    "8c_datacentre_reits": "Data-centre REITs",
    "8d_neocloud": "Neocloud",
    "8e_construction": "Construction",
    "9a_hyperscale": "Hyperscalers",
    "9b_data_platform": "Data platforms",
    "9c_edge_delivery": "Edge delivery",
    "9d_ai_native_app": "AI-native apps",
    "9e_security": "Security",
    "9f_deployment_services": "Deployment services",
    "9g_system_of_record": "Systems of record",
    "9h_design_engineering_tools": "Design & engineering tools",
    "9i_observability": "Observability",
    "9j_instruments": "Instruments",
}


def category_of(sector, sub_tier):
    """Main category: the chain layer when there is one, else the sector."""
    if sub_tier and sub_tier[:1] in CHAIN_LAYERS:
        return CHAIN_LAYERS[sub_tier[:1]]
    return sector or _UNKNOWN_SECTOR


def subcategory_of(sub_tier, sub_industry):
    """Subcategory: the chain sub-tier's name, else the GICS sub-industry."""
    if sub_tier:
        return TIER_NAMES.get(sub_tier, sub_tier)
    return sub_industry or _OTHER_SUB


def outlook_of(alert):
    if alert.get("timeframe") == "watch":
        return _WATCHES
    if alert.get("setup") and alert.get("outlook") == setups.SIDEWAYS:
        return setups.SIDEWAYS
    return setups.OUTLOOK.get(alert.get("setup"), _NO_SETUP)


def classify_alerts(alerts, universe_rows):
    """Attach outlook, category, subcategory and market_cap to each alert."""
    by_ticker = {r["ticker"]: r for r in universe_rows}
    out = []
    for a in alerts:
        meta = by_ticker.get(a["ticker"], {})
        row = dict(a)
        row["sector"] = meta.get("sector") or _UNKNOWN_SECTOR
        row["sub_tier"] = meta.get("sub_tier")
        row["category"] = category_of(row["sector"], row["sub_tier"])
        row["subcategory"] = subcategory_of(row["sub_tier"],
                                            meta.get("sub_industry"))
        row["outlook"] = outlook_of(a)
        row["market_cap"] = meta.get("market_cap") or 0.0
        out.append(row)
    return out


def _category_key(name, count):
    """Chain layers in chain order, then tech sectors, then the rest by size."""
    layers = list(CHAIN_LAYERS.values())
    if name in layers:
        return (0, layers.index(name), 0, name)
    if name in _TECH_SECTORS:
        return (1, _TECH_SECTORS.index(name), 0, name)
    if name == _UNKNOWN_SECTOR:
        return (3, 0, 0, name)
    return (2, 0, -count, name)


def group(alerts, universe_rows):
    """``{outlook: {category: {subcategory: [alerts]}}}``, every level ordered.

    Within a subcategory, alerts sort by setup (breakout first), then market
    cap descending, then ticker so the order is stable when caps are missing
    or equal.
    """
    rows = classify_alerts(alerts, universe_rows)

    nested = {}
    for r in rows:
        (nested.setdefault(r["outlook"], {})
               .setdefault(r["category"], {})
               .setdefault(r["subcategory"], []).append(r))

    ordered = OrderedDict()
    for outlook in sorted(nested, key=_OUTLOOK_ORDER.index):
        cats = nested[outlook]
        ordered[outlook] = OrderedDict()
        for cat in sorted(cats, key=lambda c: _category_key(
                c, sum(len(v) for v in cats[c].values()))):
            subs = cats[cat]
            ordered[outlook][cat] = OrderedDict()
            # The catch-all subcategory always sorts last, however many alerts
            # it holds: a named group says something, and leading with the one
            # that says nothing buries the part of the report worth reading.
            for sub in sorted(subs, key=lambda s: (s == _OTHER_SUB,
                                                   -len(subs[s]), s)):
                ordered[outlook][cat][sub] = sorted(
                    subs[sub], key=lambda a: (setups.rank(a.get("setup")),
                                              -(a["market_cap"] or 0),
                                              a["ticker"]))
    return ordered


def iter_rows(grouped):
    """``(outlook, category, subcategory, row)`` in display order."""
    for outlook, cats in grouped.items():
        for cat, subs in cats.items():
            for sub, rows in subs.items():
                for r in rows:
                    yield outlook, cat, sub, r


def clusters(grouped, min_names=3):
    """Subcategories where several distinct names alerted on the same day.

    This is the signal the grouping exists to surface: a cluster means the move
    is structural rather than idiosyncratic. The catch-all subcategory is
    skipped, since names sharing it share nothing.
    """
    found = []
    for outlook, cats in grouped.items():
        for cat, subs in cats.items():
            for sub, rows in subs.items():
                if sub == _OTHER_SUB:
                    continue
                # Largest first, matching the ordering inside the group, so a
                # name does not appear before a bigger one in the headline and
                # after it in the body.
                caps, seen = {}, []
                for r in rows:
                    if r["ticker"] not in caps:
                        seen.append(r["ticker"])
                    caps[r["ticker"]] = max(caps.get(r["ticker"], 0),
                                            r["market_cap"] or 0)
                if len(seen) >= min_names:
                    names = sorted(seen, key=lambda t: (-caps[t], t))
                    found.append({"outlook": outlook, "category": cat,
                                  "subcategory": sub, "tickers": names,
                                  "alerts": len(rows)})
    return sorted(found, key=lambda c: -len(c["tickers"]))


def format_cap(value):
    """Market cap as a short right-aligned string, shared by the digest and PDF."""
    if not value:
        return "     -"
    return f"{value / 1e9:>5.0f}B" if value >= 1e9 else f"{value / 1e6:>5.0f}M"


def outlook_count(cats):
    """Alerts under one outlook, across every category and subcategory."""
    return sum(len(rows) for subs in cats.values() for rows in subs.values())


def format_grouped(grouped, line_fn, limit=40) -> str:
    """Render the grouping as indented text for the digest or the report.

    ``line_fn`` formats one alert, so the caller decides how much detail each
    line carries without this module knowing about alert wording.
    """
    if not grouped:
        return "No alerts today."

    out, shown = [], 0
    for outlook, cats in grouped.items():
        out.append(f"== {outlook.upper()} ({outlook_count(cats)}) ==")
        for cat, subs in cats.items():
            total = sum(len(v) for v in subs.values())
            out.append(f"{cat}  ({total})")
            for sub, rows in subs.items():
                out.append(f"  {sub}")
                for r in rows:
                    if shown >= limit:
                        out.append("  ...")
                        return "\n".join(out)
                    out.append(f"    {format_cap(r['market_cap'])}  {line_fn(r)}")
                    shown += 1
        out.append("")
    return "\n".join(out).rstrip()
