"""
Supplier Discovery Engine — 360-Degree Web Search

Searches across all 5 tiers of sources to find suppliers for any
ingredient/raw material/product:

  Tier 1 — Regulatory: FDA, ECHA, CPSC, GS1
  Tier 2 — Brand/First-party: manufacturer websites, Open Food Facts
  Tier 3 — B2B Marketplace: Alibaba, IndiaMart, ThomasNet, Europages, Kompass
  Tier 4 — Trade/Customs: ImportYeti, Zauba, Panjiva
  Tier 5 — Aggregator: Google Shopping, price comparison

Returns structured supplier records ready for the supplier database.
"""

from __future__ import annotations

import re
import time
import random
import hashlib
from datetime import datetime
from typing import Optional


# ── Source definitions ────────────────────────────────────────────────────────

SEARCH_SOURCES = [
    # Tier 3 — B2B Marketplaces (highest yield for supplier discovery)
    {
        "name": "Alibaba",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} supplier manufacturer",
        "site_filter": "site:alibaba.com",
        "extract_fields": ["price", "moq", "supplier_name", "country"],
    },
    {
        "name": "IndiaMart",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} manufacturer supplier",
        "site_filter": "site:indiamart.com",
        "extract_fields": ["price", "moq", "supplier_name"],
    },
    {
        "name": "ThomasNet",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} manufacturer supplier USA",
        "site_filter": "site:thomasnet.com",
        "extract_fields": ["supplier_name", "country", "certifications"],
    },
    {
        "name": "Europages",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} supplier manufacturer Europe",
        "site_filter": "site:europages.co.uk OR site:europages.com",
        "extract_fields": ["supplier_name", "country"],
    },
    {
        "name": "Made-in-China",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} manufacturer factory",
        "site_filter": "site:made-in-china.com",
        "extract_fields": ["price", "moq", "supplier_name"],
    },
    {
        "name": "GlobalSources",
        "tier": 3,
        "type": "b2b",
        "query_template": "{query} supplier wholesale",
        "site_filter": "site:globalsources.com",
        "extract_fields": ["supplier_name", "price"],
    },
    # Tier 4 — Trade intelligence
    {
        "name": "ImportYeti",
        "tier": 4,
        "type": "trade",
        "query_template": "{query} supplier shipment",
        "site_filter": "site:importyeti.com",
        "extract_fields": ["supplier_name", "shipment_frequency", "known_buyers"],
    },
    # Tier 1 — Regulatory
    {
        "name": "FDA",
        "tier": 1,
        "type": "regulatory",
        "query_template": "{query} registered manufacturer",
        "site_filter": "site:fda.gov",
        "extract_fields": ["supplier_name", "compliance"],
    },
    {
        "name": "ECHA-REACH",
        "tier": 1,
        "type": "regulatory",
        "query_template": "{query} REACH registered",
        "site_filter": "site:echa.europa.eu",
        "extract_fields": ["supplier_name", "compliance"],
    },
    # Tier 2 — Brand/first-party
    {
        "name": "OpenFoodFacts",
        "tier": 2,
        "type": "brand",
        "query_template": "{query} brand manufacturer",
        "site_filter": "site:openfoodfacts.org",
        "extract_fields": ["supplier_name", "product"],
    },
    # General supplier search (no site filter)
    {
        "name": "General Web",
        "tier": 5,
        "type": "aggregator",
        "query_template": "{query} supplier manufacturer wholesale bulk buy",
        "site_filter": "",
        "extract_fields": ["supplier_name", "price"],
    },
]


def discover_suppliers(
    query: str,
    max_per_source: int = 5,
    progress_callback=None,
) -> list[dict]:
    """
    Search all sources for suppliers of the given ingredient/product.

    Args:
        query: Ingredient or product name (e.g. "soy lecithin", "PET bottle 600ml")
        max_per_source: Max results per search source
        progress_callback: Optional callable(step, total, message)

    Returns:
        List of supplier dicts ready for SupplierDatabase.add_supplier()
    """
    try:
        from data_collection.search_engine import multi_engine_search
    except ImportError:
        return []

    all_suppliers = []
    seen_names = set()
    total_steps = len(SEARCH_SOURCES)

    for i, source in enumerate(SEARCH_SOURCES):
        if progress_callback:
            progress_callback(i, total_steps,
                              f"Searching {source['name']}...")

        # Build search query
        search_q = source["query_template"].format(query=query)
        if source["site_filter"]:
            search_q = f"{search_q} {source['site_filter']}"

        try:
            results = multi_engine_search(
                search_q,
                max_per_engine=max_per_source,
                use_ddg=True,
                use_bing=True,
                use_google=False,
            )
            valid = [r for r in results if "error" not in r and r.get("url")]
        except Exception:
            valid = []

        # Parse each result into a supplier record
        for r in valid[:max_per_source]:
            supplier = _parse_search_result(r, source, query)
            if supplier and supplier["supplier_name"]:
                # Deduplicate by normalized name
                norm_name = supplier["supplier_name"].lower().strip()
                if norm_name not in seen_names and len(norm_name) > 2:
                    seen_names.add(norm_name)
                    all_suppliers.append(supplier)

        # Polite delay
        time.sleep(random.uniform(0.4, 1.0))

    if progress_callback:
        progress_callback(total_steps, total_steps,
                          f"Found {len(all_suppliers)} suppliers")

    return all_suppliers


def _parse_search_result(result: dict, source: dict, query: str) -> Optional[dict]:
    """Parse a web search result into a supplier record."""
    url = result.get("url", "")
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    domain = result.get("domain", "")

    # Extract supplier name from title
    supplier_name = _extract_supplier_name(title, domain, source["name"])
    if not supplier_name:
        return None

    # Extract country
    country = _extract_country(title + " " + snippet)

    # Extract price info
    price, currency = _extract_price(snippet)

    # Extract MOQ
    moq = _extract_moq(snippet)

    # Check for certification mentions
    certs = _extract_certs(title + " " + snippet)

    # Build supplier dict
    supplier = {
        "supplier_name": supplier_name,
        "product": query,
        "product_category": "",
        "country": country,
        "price_per_unit": price,
        "currency": currency or "USD",
        "moq": moq,
        "source_tier": source["tier"],
        "source_name": source["name"],
        "source_url": url,
        "source_type": source["type"],
        "date_scraped": datetime.now().strftime("%Y-%m-%d"),
        "scraped_by": "Agnes Auto-Discovery",
        # Certs
        "cert_iso": certs.get("iso", False),
        "cert_haccp": certs.get("haccp", False),
        "cert_reach": certs.get("reach", False),
        "cert_ce_mark": certs.get("ce", False),
        "cert_fssai": certs.get("fssai", False),
        "cert_brc": certs.get("brc", False),
        # Triangulation starts incomplete
        "triangulation_regulatory": source["tier"] == 1,
        "triangulation_firstparty": source["tier"] == 2,
        "triangulation_trade": source["tier"] == 4,
        "triangulation_complete": False,
        # Website
        "website": url if source["tier"] == 2 else "",
    }

    return supplier


def _extract_supplier_name(title: str, domain: str, source_name: str) -> str:
    """Extract a clean supplier/company name from a search result title."""
    if not title:
        return ""

    name = title

    # Remove common suffixes/prefixes from marketplace titles
    remove_patterns = [
        r"\s*[-–|]\s*Alibaba\.com.*$",
        r"\s*[-–|]\s*IndiaMART.*$",
        r"\s*[-–|]\s*ThomasNet.*$",
        r"\s*[-–|]\s*Europages.*$",
        r"\s*[-–|]\s*Made-in-China.*$",
        r"\s*[-–|]\s*Global Sources.*$",
        r"\s*[-–|]\s*ImportYeti.*$",
        r"\s*[-–|]\s*FDA.*$",
        r"\s*[-–|]\s*Amazon.*$",
        r"\s*[-–|]\s*eBay.*$",
        r"\s*on Alibaba\.com$",
        r"^Buy\s+",
        r"^Wholesale\s+",
        r"\s*\|\s*.*$",
        r"\s*-\s*Products,?\s*Suppliers.*$",
        r"\s*-\s*Manufacturer.*$",
        r"\s*Supplier\s*$",
        r"\s*Manufacturer\s*$",
    ]
    for pat in remove_patterns:
        name = re.sub(pat, "", name, flags=re.IGNORECASE)

    # Try to extract company name (usually before a dash or pipe)
    parts = re.split(r"\s*[-–|:]\s*", name)
    if len(parts) >= 2:
        # Usually the company name is the first or second part
        # If first part looks like a product description, use second
        if len(parts[0]) > 60 or any(
            w in parts[0].lower() for w in ["buy", "price", "wholesale", "best"]
        ):
            name = parts[1].strip()
        else:
            name = parts[0].strip()

    # Clean up
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:80]  # cap length

    # Skip if it's clearly not a company name
    skip_words = ["search results", "error", "not found", "page", "login",
                  "sign up", "register"]
    if any(w in name.lower() for w in skip_words):
        return ""

    return name


def _extract_country(text: str) -> str:
    """Extract country from text."""
    country_patterns = {
        "China": r"\b(?:China|CN|Chinese|Mainland China|Guangdong|Zhejiang|Shanghai|Shandong)\b",
        "India": r"\b(?:India|IN|Indian|Mumbai|Delhi|Chennai|Gujarat|Maharashtra)\b",
        "USA": r"\b(?:USA|United States|US|American|California|Texas|New York|Illinois)\b",
        "Germany": r"\b(?:Germany|DE|German|Deutschland)\b",
        "Japan": r"\b(?:Japan|JP|Japanese|Tokyo)\b",
        "South Korea": r"\b(?:South Korea|Korea|KR|Korean|Seoul)\b",
        "UK": r"\b(?:United Kingdom|UK|British|England|London)\b",
        "France": r"\b(?:France|FR|French|Paris)\b",
        "Italy": r"\b(?:Italy|IT|Italian|Milano)\b",
        "Netherlands": r"\b(?:Netherlands|NL|Dutch|Amsterdam)\b",
        "Taiwan": r"\b(?:Taiwan|TW|Taiwanese|Taipei)\b",
        "Thailand": r"\b(?:Thailand|TH|Thai|Bangkok)\b",
        "Vietnam": r"\b(?:Vietnam|VN|Vietnamese|Hanoi)\b",
        "Brazil": r"\b(?:Brazil|BR|Brazilian)\b",
        "Mexico": r"\b(?:Mexico|MX|Mexican)\b",
        "Turkey": r"\b(?:Turkey|TR|Turkish|Istanbul)\b",
        "Indonesia": r"\b(?:Indonesia|ID|Indonesian|Jakarta)\b",
        "Malaysia": r"\b(?:Malaysia|MY|Malaysian|Kuala Lumpur)\b",
        "Bangladesh": r"\b(?:Bangladesh|BD|Bangladeshi|Dhaka)\b",
        "Pakistan": r"\b(?:Pakistan|PK|Pakistani|Karachi|Lahore)\b",
    }
    for country, pattern in country_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return country
    return ""


def _extract_price(text: str) -> tuple[Optional[float], Optional[str]]:
    """Extract price and currency from text."""
    patterns = [
        (r"\$\s*(\d+(?:\.\d+)?)", "USD"),
        (r"USD\s*(\d+(?:\.\d+)?)", "USD"),
        (r"€\s*(\d+(?:\.\d+)?)", "EUR"),
        (r"EUR\s*(\d+(?:\.\d+)?)", "EUR"),
        (r"£\s*(\d+(?:\.\d+)?)", "GBP"),
        (r"₹\s*(\d+(?:\.\d+)?)", "INR"),
        (r"INR\s*(\d+(?:\.\d+)?)", "INR"),
        (r"¥\s*(\d+(?:\.\d+)?)", "CNY"),
        (r"(\d+(?:\.\d+)?)\s*/\s*(?:piece|pc|unit|kg|ton|lb)", "USD"),
    ]
    for pattern, currency in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)), currency
            except ValueError:
                pass
    return None, None


def _extract_moq(text: str) -> Optional[int]:
    """Extract minimum order quantity from text."""
    patterns = [
        r"(?:MOQ|min(?:imum)?\s*order)\s*[:\s]*(\d[\d,]*)\s*(?:pcs?|pieces?|units?|kg|tons?)?",
        r"(\d[\d,]*)\s*(?:pcs?|pieces?|units?)\s*(?:min|minimum)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def _extract_certs(text: str) -> dict:
    """Extract certification mentions from text."""
    certs = {}
    cert_patterns = {
        "iso": r"\bISO\s*(?:9001|22000|14001|10377)\b",
        "haccp": r"\bHACCP\b",
        "reach": r"\bREACH\b",
        "ce": r"\bCE\s*(?:mark|certified|marking)?\b",
        "fssai": r"\bFSSAI\b",
        "brc": r"\bBRC(?:GS)?\b",
        "gmp": r"\bGMP\b",
        "fda": r"\bFDA\s*(?:approved|registered|compliant)\b",
    }
    text_lower = text.lower()
    for cert_id, pattern in cert_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            certs[cert_id] = True
    return certs


def discover_for_ingredients(
    ingredients: list[str],
    max_per_source: int = 3,
    progress_callback=None,
) -> dict[str, list[dict]]:
    """
    Discover suppliers for a list of ingredients (e.g. from a barcode scan).

    Returns dict mapping ingredient name to list of supplier dicts.
    """
    results = {}
    total = len(ingredients)
    for i, ingredient in enumerate(ingredients):
        if progress_callback:
            progress_callback(i, total,
                              f"Discovering suppliers for: {ingredient}")
        suppliers = discover_suppliers(
            ingredient,
            max_per_source=max_per_source,
            progress_callback=None,  # suppress inner progress
        )
        results[ingredient] = suppliers

    if progress_callback:
        progress_callback(total, total, "Discovery complete")

    return results
