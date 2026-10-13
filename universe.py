import requests
from bs4 import BeautifulSoup

from monitor_config import MONITOR


def parse_constituents(html) -> list:
    """(ticker, name, sector, sub_industry) from the constituents table, sorted.

    Wikipedia writes class-B tickers as BRK.B; yfinance wants BRK-B. Converting
    here means every downstream caller sees one convention.

    The GICS sector is column three and was previously discarded. It is the only
    free, authoritative classification available for the index, and the digest
    groups on it — worth the two extra cells. The GICS sub-industry is column
    four, and is the subcategory for names outside the AI chain.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise ValueError("no table with id 'constituents' in the page")

    found = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue                      # header row, or a malformed one
        ticker = cells[0].get_text(strip=True).replace(".", "-")
        name = cells[1].get_text(strip=True)
        sector = cells[2].get_text(strip=True) if len(cells) > 2 else None
        sub = cells[3].get_text(strip=True) if len(cells) > 3 else None
        if ticker:
            found[ticker] = (name, sector, sub or None)
    return sorted((t, *rest) for t, rest in found.items())


def fetch_html(url=None, timeout=30) -> str:
    """Download the constituents page. The only network call in this module."""
    resp = requests.get(url or MONITOR.sp500_url,
                        headers={"User-Agent": "sp500-monitor/1.0"},
                        timeout=timeout)
    resp.raise_for_status()
    return resp.text


def diff_universe(current, fetched) -> tuple:
    """(added, removed) comparing a freshly scraped list against the stored one.

    An empty ``fetched`` removes nothing. A scrape returning zero tickers means
    the page moved or the request failed — retiring all 500 constituents on that
    basis would destroy the universe on a transient error.
    """
    if not fetched:
        return [], []
    current_set, fetched_set = set(current), set(fetched)
    return sorted(fetched_set - current_set), sorted(current_set - fetched_set)
