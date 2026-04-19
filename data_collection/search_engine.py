"""
Multi-Engine Web Search (Zero LLM)
Queries DuckDuckGo, Google (via scraping), and Bing to discover supplier URLs.
No API keys required.
"""

import time
import random
import re
from typing import Optional
from urllib.parse import urlparse, quote_plus

# Rotating User-Agent pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Known high-value supplier domains to prioritize
PRIORITY_DOMAINS = {
    # Industrial / Fasteners
    "mcmaster.com":     {"type": "industrial_distributor", "quality": 5, "region": "US"},
    "grainger.com":     {"type": "industrial_distributor", "quality": 5, "region": "US"},
    "fastenal.com":     {"type": "fastener_distributor",   "quality": 5, "region": "US"},
    "mscdirect.com":    {"type": "industrial_distributor", "quality": 5, "region": "US"},
    "rs-online.com":    {"type": "industrial_distributor", "quality": 5, "region": "GLOBAL"},
    "rscomponents.com": {"type": "industrial_distributor", "quality": 5, "region": "GLOBAL"},
    "wurth.com":        {"type": "fastener_manufacturer",  "quality": 5, "region": "EU"},
    "bossard.com":      {"type": "fastener_distributor",   "quality": 4, "region": "EU"},
    "boltdepot.com":    {"type": "fastener_distributor",   "quality": 4, "region": "US"},
    "globalindustrial.com": {"type": "industrial_distributor", "quality": 4, "region": "US"},
    "zoro.com":         {"type": "industrial_distributor", "quality": 4, "region": "US"},
    "mscdirect.com":    {"type": "industrial_distributor", "quality": 5, "region": "US"},
    # Electronics
    "digikey.com":      {"type": "electronics_distributor","quality": 5, "region": "GLOBAL"},
    "mouser.com":       {"type": "electronics_distributor","quality": 5, "region": "GLOBAL"},
    "arrow.com":        {"type": "electronics_distributor","quality": 5, "region": "GLOBAL"},
    "farnell.com":      {"type": "electronics_distributor","quality": 5, "region": "EU"},
    "uk.farnell.com":   {"type": "electronics_distributor","quality": 5, "region": "EU"},
    "element14.com":    {"type": "electronics_distributor","quality": 5, "region": "GLOBAL"},
    # Bearings
    "skf.com":          {"type": "bearing_manufacturer",   "quality": 5, "region": "GLOBAL"},
    "nsk.com":          {"type": "bearing_manufacturer",   "quality": 5, "region": "GLOBAL"},
    "schaeffler.com":   {"type": "bearing_manufacturer",   "quality": 5, "region": "EU"},
    "timken.com":       {"type": "bearing_manufacturer",   "quality": 5, "region": "US"},
    "ntn.com":          {"type": "bearing_manufacturer",   "quality": 5, "region": "GLOBAL"},
    # Marketplaces
    "alibaba.com":      {"type": "b2b_marketplace",        "quality": 3, "region": "GLOBAL"},
    "aliexpress.com":   {"type": "retail_marketplace",     "quality": 2, "region": "GLOBAL"},
    "indiamart.com":    {"type": "b2b_marketplace",        "quality": 3, "region": "IN"},
    "tradeindia.com":   {"type": "b2b_marketplace",        "quality": 3, "region": "IN"},
    "thomasnet.com":    {"type": "b2b_directory",          "quality": 4, "region": "US"},
    "made-in-china.com":{"type": "b2b_marketplace",        "quality": 3, "region": "CN"},
    "globalsources.com":{"type": "b2b_marketplace",        "quality": 3, "region": "CN"},
    "amazon.com":       {"type": "retail_marketplace",     "quality": 2, "region": "US"},
    "amazon.co.uk":     {"type": "retail_marketplace",     "quality": 2, "region": "UK"},
}

EXCLUDED_DOMAINS = {
    "wikipedia.org", "youtube.com", "facebook.com", "twitter.com", "reddit.com",
    "quora.com", "linkedin.com", "instagram.com", "pinterest.com", "yelp.com",
    "stackoverflow.com", "github.com"
}


def search_duckduckgo(query: str, max_results: int = 15) -> list[dict]:
    """
    Search DuckDuckGo using the duckduckgo-search library.
    Returns list of {url, title, snippet, domain, priority_info}.
    """
    results = []
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        for r in raw:
            url = r.get("href", "")
            domain = _extract_domain(url)
            if domain in EXCLUDED_DOMAINS:
                continue
            results.append({
                "url": url,
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "domain": domain,
                "source": "duckduckgo",
                "priority": PRIORITY_DOMAINS.get(domain, {}).get("quality", 1)
            })
    except Exception as e:
        results.append({"error": str(e), "source": "duckduckgo"})
    return results


def search_bing(query: str, max_results: int = 10) -> list[dict]:
    """
    Scrape Bing search results (no API key required).
    """
    results = []
    try:
        import requests
        from bs4 import BeautifulSoup

        url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        for li in soup.select("li.b_algo"):
            h2 = li.find("h2")
            if not h2:
                continue
            a = h2.find("a")
            if not a:
                continue
            href = a.get("href", "")
            title = a.get_text(strip=True)
            snippet_el = li.find("p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            domain = _extract_domain(href)
            if domain in EXCLUDED_DOMAINS or not href.startswith("http"):
                continue
            results.append({
                "url": href, "title": title, "snippet": snippet,
                "domain": domain, "source": "bing",
                "priority": PRIORITY_DOMAINS.get(domain, {}).get("quality", 1)
            })

    except Exception as e:
        results.append({"error": str(e), "source": "bing"})
    return results


def search_google(query: str, max_results: int = 10) -> list[dict]:
    """
    Scrape Google search results.
    Note: Google may block automated requests; DDG is preferred.
    """
    results = []
    try:
        import requests
        from bs4 import BeautifulSoup

        url = f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}"
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        for div in soup.select("div.g"):
            a = div.find("a")
            if not a:
                continue
            href = a.get("href", "")
            if not href.startswith("http"):
                continue
            h3 = div.find("h3")
            title = h3.get_text(strip=True) if h3 else href
            snippet_el = div.find("div", {"data-sncf": True}) or div.find("span", class_="aCOpRe")
            snippet = snippet_el.get_text(strip=True)[:200] if snippet_el else ""
            domain = _extract_domain(href)
            if domain in EXCLUDED_DOMAINS:
                continue
            results.append({
                "url": href, "title": title, "snippet": snippet,
                "domain": domain, "source": "google",
                "priority": PRIORITY_DOMAINS.get(domain, {}).get("quality", 1)
            })

    except Exception as e:
        results.append({"error": str(e), "source": "google"})
    return results


def search_google_shopping(query: str, max_results: int = 10) -> list[dict]:
    """
    Scrape Google Shopping results for price comparison.
    """
    results = []
    try:
        import requests
        from bs4 import BeautifulSoup

        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&num={max_results}"
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select("div.sh-dgr__content"):
            name_el = item.find("h4") or item.find("h3")
            price_el = item.find("span", class_=re.compile(r"price|Price"))
            seller_el = item.find("div", class_=re.compile(r"merchant|seller|store"))
            name = name_el.get_text(strip=True) if name_el else ""
            price = price_el.get_text(strip=True) if price_el else ""
            seller = seller_el.get_text(strip=True) if seller_el else ""
            if name:
                results.append({
                    "name": name, "price_raw": price, "seller": seller,
                    "source": "google_shopping"
                })
    except Exception as e:
        results.append({"error": str(e), "source": "google_shopping"})
    return results


def multi_engine_search(
    query: str,
    max_per_engine: int = 10,
    use_ddg: bool = True,
    use_bing: bool = True,
    use_google: bool = False,
    deduplicate: bool = True
) -> list[dict]:
    """
    Run query across multiple search engines and combine results.
    Deduplicates by URL, sorts by priority (known supplier domains first).
    """
    all_results = []

    if use_ddg:
        ddg_results = search_duckduckgo(query, max_per_engine)
        all_results.extend([r for r in ddg_results if "error" not in r])
        time.sleep(random.uniform(0.5, 1.2))

    if use_bing:
        bing_results = search_bing(query, max_per_engine)
        all_results.extend([r for r in bing_results if "error" not in r])
        time.sleep(random.uniform(0.3, 0.8))

    if use_google:
        g_results = search_google(query, max_per_engine)
        all_results.extend([r for r in g_results if "error" not in r])
        time.sleep(random.uniform(1.0, 2.0))

    # Deduplicate by URL
    if deduplicate:
        seen_urls = set()
        seen_domains = {}
        deduped = []
        for r in all_results:
            url = r.get("url", "")
            domain = r.get("domain", "")
            if url in seen_urls:
                continue
            # Max 3 results per domain (avoid one distributor swamping)
            if seen_domains.get(domain, 0) >= 3:
                continue
            seen_urls.add(url)
            seen_domains[domain] = seen_domains.get(domain, 0) + 1
            deduped.append(r)
        all_results = deduped

    # Sort: known suppliers first, then by priority score
    all_results.sort(key=lambda r: -(r.get("priority", 1)))

    return all_results


def classify_url(url: str) -> dict:
    """Classify a URL — what kind of source is it?"""
    domain = _extract_domain(url)
    if domain in PRIORITY_DOMAINS:
        info = PRIORITY_DOMAINS[domain].copy()
        info["domain"] = domain
        info["known"] = True
        return info
    return {
        "domain": domain,
        "type": "unknown",
        "quality": 1,
        "region": "UNKNOWN",
        "known": False
    }


def _extract_domain(url: str) -> str:
    """Extract root domain from URL."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        # Remove www. prefix
        return re.sub(r'^www\.', '', host)
    except Exception:
        return ""


if __name__ == "__main__":
    print("Testing search engines with query: 'M5 bolt supplier price'")
    results = multi_engine_search("M5 bolt 30mm stainless steel supplier", max_per_engine=8)
    print(f"Total results: {len(results)}")
    for r in results[:10]:
        print(f"  [{r.get('priority', 1)}] {r.get('domain', '?')[:30]:30} {r.get('title', '')[:50]}")
