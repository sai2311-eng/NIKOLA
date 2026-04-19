"""
IndiaMART Scraper
IndiaMART (indiamart.com) — India's largest B2B marketplace.
Critical for local Asian suppliers not on Alibaba.
"""

import re
import json
from bs4 import BeautifulSoup
from ..distributor_scrapers.base_scraper import BaseScraper


class IndiaMArtScraper(BaseScraper):
    name = "indiamart"
    base_url = "https://www.indiamart.com"
    rate_limit_seconds = 2.5

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search IndiaMART for a product."""
        results = []
        search_url = f"{self.base_url}/proddetail/{query.replace(' ', '-').lower()}.html"
        alt_url    = f"https://dir.indiamart.com/search.mp?ss={query.replace(' ', '+')}"

        for url in [alt_url, search_url]:
            html = self._fetch(url)
            if html:
                page_results = self._parse_search_html(html, max_results)
                results.extend(page_results)
                if results:
                    break

        return results[:max_results]

    def scrape_product_page(self, url: str) -> dict:
        """Scrape an IndiaMART product listing page."""
        html = self._fetch(url)
        if not html:
            return self._canonical_result(source_url=url, supplier="IndiaMART")

        soup = BeautifulSoup(html, "html.parser")
        result = self._canonical_result(source_url=url, supplier="IndiaMART")

        # Product title
        h1 = soup.find("h1")
        if h1:
            result["name"] = h1.get_text(strip=True)[:120]

        # Company / manufacturer
        company_el = soup.find(class_=re.compile(r"company|seller|supplier", re.I)) or \
                     soup.find("a", href=re.compile(r"/company/"))
        if company_el:
            result["manufacturer"] = company_el.get_text(strip=True)[:80]

        # Price
        price_el = soup.find(class_=re.compile(r"price|Price"))
        if price_el:
            price_text = price_el.get_text(strip=True)
            # IndiaMART prices in INR usually
            prices = re.findall(r'[\d,]+\.?\d*', price_text)
            if prices:
                result["price_per_unit"] = float(prices[0].replace(",", ""))
                result["price_currency"] = "INR"

        # MOQ
        moq_text = soup.get_text()
        moq_match = re.search(r'(?:MOQ|Min\.\s*Order|Minimum\s*Order)[:\s]+(\d[\d,]*)\s*(?:Piece|Nos|Units?|pcs?)', moq_text, re.I)
        if moq_match:
            result["moq"] = int(moq_match.group(1).replace(",", ""))

        # Lead time
        lt_match = re.search(r'(?:lead\s*time|delivery)[:\s]+(\d+[-–]\d+|\d+)\s*(?:days?|weeks?)', moq_text, re.I)
        if lt_match:
            result["lead_time_days"] = self._parse_lead_time(lt_match.group())

        # Specifications
        specs = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    k = cells[0].get_text(strip=True).lower().rstrip(":")
                    v = cells[1].get_text(strip=True)
                    if k and v and len(k) < 60:
                        specs[k] = v
        if specs:
            result["specifications"] = specs
            for k in ["material", "grade", "surface finish"]:
                if k in specs:
                    result[k] = specs[k]
                    break

        # Certifications
        certs = []
        page_text = soup.get_text()
        for cert in ["ISO 9001", "ISO 14001", "CE", "BIS", "ASTM", "DIN", "RoHS"]:
            if re.search(r'\b' + re.escape(cert) + r'\b', page_text, re.I):
                certs.append(cert)
        result["certifications"] = certs

        result["confidence_score"] = 0.60
        return result

    def _parse_search_html(self, html: str, max_results: int) -> list[dict]:
        results = []
        soup = BeautifulSoup(html, "html.parser")

        # IndiaMART search result listings
        listings = soup.select(".list-cat-pg, .product-title, .product-listing")
        if not listings:
            listings = soup.select("h2 a, h3 a, .prd-name a")

        for item in listings[:max_results]:
            r = self._canonical_result(supplier="IndiaMART")
            if item.name == "a":
                r["name"] = item.get_text(strip=True)[:100]
                href = item.get("href", "")
                r["source_url"] = href if href.startswith("http") else self.base_url + href
            else:
                link = item.find("a")
                if link:
                    r["name"] = link.get_text(strip=True)[:100]
                    href = link.get("href", "")
                    r["source_url"] = href if href.startswith("http") else self.base_url + href
            if r.get("name"):
                r["confidence_score"] = 0.50
                results.append(r)
        return results
