"""
ThomasNet Scraper
ThomasNet (thomasnet.com) — North America's industrial supplier directory.
Lists thousands of small/regional manufacturers not on Alibaba or Grainger.
Critical for covering the "local traders" blind spot.
"""

import re
from bs4 import BeautifulSoup
from ..distributor_scrapers.base_scraper import BaseScraper


class ThomasNetScraper(BaseScraper):
    name = "thomasnet"
    base_url = "https://www.thomasnet.com"
    rate_limit_seconds = 2.0

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search ThomasNet supplier directory."""
        results = []
        search_url = f"{self.base_url}/products/{query.replace(' ', '-').lower()}/"
        html = self._fetch(search_url)
        if not html:
            # Try alternate URL format
            search_url = f"{self.base_url}/nsearch/products/1?qt={query.replace(' ', '+')}"
            html = self._fetch(search_url)
        if not html:
            return results

        soup = BeautifulSoup(html, "html.parser")

        # ThomasNet supplier cards
        supplier_cards = soup.select(".supplier-card, .card--profile, .company-card") or \
                         soup.select("[data-testid='supplier-card'], article[class*='profile']")

        for card in supplier_cards[:max_results]:
            try:
                r = self._parse_supplier_card(card, query)
                if r:
                    results.append(r)
            except Exception:
                continue

        return results

    def scrape_product_page(self, url: str) -> dict:
        """Scrape a ThomasNet supplier/product page."""
        html = self._fetch(url)
        if not html:
            return self._canonical_result(source_url=url, supplier="ThomasNet")

        soup = BeautifulSoup(html, "html.parser")
        result = self._canonical_result(source_url=url, supplier="ThomasNet")

        # Company name (ThomasNet is a directory, so "name" = company + product)
        h1 = soup.find("h1")
        if h1:
            result["name"] = h1.get_text(strip=True)[:120]

        company_el = soup.find(class_=re.compile(r"company-name|supplier-name", re.I))
        if company_el:
            result["manufacturer"] = company_el.get_text(strip=True)[:80]

        # Products listed by this supplier
        product_sections = soup.select(".product-list li, .products-list a")
        products = []
        for p in product_sections[:20]:
            products.append(p.get_text(strip=True))
        if products:
            result["description"] = f"Products: {', '.join(products[:5])}"

        # Certifications / standards
        cert_text = soup.get_text()
        certs = []
        for cert in ["ISO 9001", "AS9100", "IATF 16949", "ISO 14001", "RoHS", "ASTM", "ANSI", "DIN", "CE", "UL"]:
            if re.search(r'\b' + re.escape(cert) + r'\b', cert_text, re.I):
                certs.append(cert)
        result["certifications"] = certs

        # Annual revenue / company size hint
        revenue_match = re.search(r'\$[\d.]+\s*(?:Million|Billion)', cert_text, re.I)
        if revenue_match:
            result["company_revenue"] = revenue_match.group()

        result["confidence_score"] = 0.55
        return result

    def _parse_supplier_card(self, card, query: str) -> dict:
        r = self._canonical_result(supplier="ThomasNet")

        name_el = card.find(["h3", "h4", "h2"])
        if name_el:
            company = name_el.get_text(strip=True)
            r["manufacturer"] = company[:80]
            r["name"] = f"{query} — {company}"

        link = card.find("a", href=True)
        if link:
            href = link["href"]
            r["source_url"] = href if href.startswith("http") else self.base_url + href

        # Location
        loc_el = card.find(class_=re.compile(r"location|address", re.I))
        if loc_el:
            r["location"] = loc_el.get_text(strip=True)[:80]

        # Certifications on card
        card_text = card.get_text()
        certs = []
        for cert in ["ISO 9001", "AS9100", "RoHS", "IATF"]:
            if cert in card_text:
                certs.append(cert)
        r["certifications"] = certs

        if r.get("name"):
            r["confidence_score"] = 0.50
            return r
        return None
