"""
Alibaba / AliExpress Scraper
Uses Selenium because Alibaba is heavily JavaScript-rendered.
Falls back to requests+BS4 for basic info.
"""

import re
import json
import time
from bs4 import BeautifulSoup
from ..distributor_scrapers.base_scraper import BaseScraper


class AlibabaScraper(BaseScraper):
    name = "alibaba"
    base_url = "https://www.alibaba.com"
    requires_selenium = True
    rate_limit_seconds = 3.0

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search Alibaba for a component."""
        results = []
        search_url = f"{self.base_url}/trade/search?SearchText={query.replace(' ', '+')}&f=p"

        # Try requests first (may get partial data)
        html = self._fetch(search_url)
        if html:
            results = self._parse_search_html(html, max_results)

        # If insufficient results, try Selenium
        if len(results) < 3 and self.requires_selenium:
            html_sel = self._fetch_selenium(search_url, wait_seconds=4)
            if html_sel:
                sel_results = self._parse_search_html(html_sel, max_results)
                # Merge, dedup by name
                existing_names = {r.get("name", "")[:30] for r in results}
                for r in sel_results:
                    if r.get("name", "")[:30] not in existing_names:
                        results.append(r)

        return results[:max_results]

    def scrape_product_page(self, url: str) -> dict:
        """Scrape an Alibaba product listing page."""
        html = self._fetch_selenium(url, wait_seconds=4) or self._fetch(url)
        if not html:
            return self._canonical_result(source_url=url, supplier="Alibaba")

        soup = BeautifulSoup(html, "html.parser")
        result = self._canonical_result(source_url=url, supplier="Alibaba")

        # Product title
        h1 = soup.find("h1")
        if h1:
            result["name"] = h1.get_text(strip=True)[:120]

        # Price (Alibaba uses range: $0.10 - $0.50 / piece)
        price_el = soup.find(class_=re.compile(r"price|Price")) or \
                   soup.find(attrs={"data-price": True})
        if price_el:
            price_text = price_el.get_text(strip=True)
            # Extract min price from range
            prices = re.findall(r'[\$€£]?\s*(\d+\.?\d*)', price_text)
            if prices:
                result["price_per_unit"] = float(prices[0])
                result["price_currency"] = "USD"

        # MOQ
        moq_el = soup.find(text=re.compile(r'Min\.\s*Order|Minimum\s*Order', re.I))
        if moq_el:
            moq_match = re.search(r'(\d[\d,]*)', str(moq_el))
            if moq_match:
                result["moq"] = int(moq_match.group(1).replace(",", ""))

        # Lead time / delivery
        lt_el = soup.find(text=re.compile(r'lead\s*time|delivery|days to ship', re.I))
        if lt_el:
            result["lead_time_days"] = self._parse_lead_time(str(lt_el))

        # Supplier / manufacturer
        supplier_el = soup.find(class_=re.compile(r"company|supplier|seller"))
        if supplier_el:
            result["manufacturer"] = supplier_el.get_text(strip=True)[:80]

        # Certifications (often shown on Alibaba)
        cert_text = soup.get_text()
        certs = []
        for cert in ["CE", "RoHS", "ISO 9001", "SGS", "REACH", "UL", "ASTM"]:
            if re.search(r'\b' + re.escape(cert) + r'\b', cert_text, re.I):
                certs.append(cert)
        result["certifications"] = certs
        result["compliance"] = certs

        # Product details / specs table
        specs = {}
        for row in soup.select("tr, .detail-item"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                k = cells[0].get_text(strip=True).lower().rstrip(":")
                v = cells[1].get_text(strip=True)
                if k and v and k != v and len(k) < 60:
                    specs[k] = v
        if specs:
            result["specifications"] = specs
            for mat_key in ["material", "surface treatment", "grade"]:
                if mat_key in specs:
                    result[mat_key] = specs[mat_key]

        result["confidence_score"] = 0.65
        return result

    def _parse_search_html(self, html: str, max_results: int) -> list[dict]:
        results = []
        soup = BeautifulSoup(html, "html.parser")

        # Multiple card patterns Alibaba uses
        cards = soup.select(".offer-item, .J-offer-item, .gallery-offer-item, .organic-offer-item")
        if not cards:
            cards = soup.select("[data-spm*='offer'], [class*='offer-item']")

        for card in cards[:max_results]:
            try:
                r = self._canonical_result(supplier="Alibaba")
                title = card.find(["h4", "h3", "a", "div"], class_=re.compile(r"title|name", re.I))
                if title:
                    r["name"] = title.get_text(strip=True)[:100]
                link = card.find("a", href=True)
                if link:
                    href = link["href"]
                    r["source_url"] = href if href.startswith("http") else "https:" + href
                price_el = card.find(class_=re.compile(r"price", re.I))
                if price_el:
                    prices = re.findall(r'(\d+\.?\d*)', price_el.get_text())
                    if prices:
                        r["price_per_unit"] = float(prices[0])
                        r["price_currency"] = "USD"
                moq_el = card.find(text=re.compile(r'min\.\s*\d+|moq', re.I))
                if moq_el:
                    moq_match = re.search(r'(\d+)', str(moq_el))
                    if moq_match:
                        r["moq"] = int(moq_match.group(1))
                if r.get("name"):
                    r["confidence_score"] = 0.55
                    results.append(r)
            except Exception:
                continue
        return results
