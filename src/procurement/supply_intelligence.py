"""
Supply Intelligence Gatherer — Stage 4 of the Procurement Pipeline.

Orchestrates 11 data layers in parallel.  Each layer returns a list of
supplier dicts with a common schema.  Layers that have dedicated scrapers
(Alibaba, IndiaMART, ThomasNet) use those first; all others fall back to
web-search + snippet parsing.

Modes
-----
offline : Selenium / web scraping only — no LLM API needed
api     : Same as offline, plus Claude Haiku post-processes raw text for
          higher-quality supplier extraction

Layer map
---------
 1  Trade & Customs Data          (Panjiva, Import Yeti, customs DB)
 2  B2B Industrial Directories    (ThomasNet, Europages, Kompass)
 3  Govt & Regulatory Databases   (standards bodies, QVLs)
 4  Technical Documents           (spec sheets, datasheets via PDFHarvester)
 5  Patents                       (Google Patents, Espacenet)
 6  Trade Show & Exhibitions      (Hannover Messe, trade fair directories)
 7  LinkedIn & Associate Dirs     (company profiles, employee signals)
 8  Export Promotion Councils     (EEPC, FIEO, CHEMEXCIL, etc.)
 9  Alibaba                       (AlibabaScraper)
10  IndiaMART                     (IndiamartScraper)
11  Specialized / Raw Mtrl Reports (market research, analyst reports)
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

# Ensure project root is importable
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── safe imports with fallbacks ───────────────────────────────────────────────
try:
    from ddgs import DDGS as _DDGS
    _SEARCH_OK = True
except ImportError:
    try:
        from duckduckgo_search import DDGS as _DDGS
        _SEARCH_OK = True
    except ImportError:
        _DDGS = None
        _SEARCH_OK = False

try:
    from data_collection.marketplace_scrapers.alibaba_scraper import AlibabaScraper
    _ALIBABA_OK = True
except ImportError:
    _ALIBABA_OK = False

try:
    from data_collection.marketplace_scrapers.indiamart_scraper import IndiamartScraper
    _INDIAMART_OK = True
except ImportError:
    _INDIAMART_OK = False

try:
    from data_collection.marketplace_scrapers.thomasnet_scraper import ThomasnetScraper
    _THOMASNET_OK = True
except ImportError:
    _THOMASNET_OK = False

try:
    from data_collection.pdf_harvester import PDFHarvester
    _PDF_OK = True
except ImportError:
    _PDF_OK = False

try:
    from data_extraction.spec_extractor import SpecExtractor
    _EXTRACTOR_OK = True
except ImportError:
    _EXTRACTOR_OK = False


# ── common supplier record schema ─────────────────────────────────────────────
def _make_supplier(
    name: str,
    website: str = "",
    region: str = "unknown",
    layer: int = 0,
    layer_name: str = "",
    material: str = "",
    price_usd=None,
    moq=None,
    lead_time_days=None,
    certifications: list = None,
    quality_rating=None,
    snippet: str = "",
    confidence: float = 0.4,
    **extra,
) -> dict:
    return {
        "supplier_name": name,
        "website": website,
        "region": region,
        "layer": layer,
        "layer_name": layer_name,
        "material": material,
        "price_usd": price_usd,
        "moq": moq,
        "lead_time_days": lead_time_days,
        "certifications": certifications or [],
        "quality_rating": quality_rating,
        "snippet": snippet,
        "confidence": confidence,
        **extra,
    }


class SupplyIntelligenceGatherer:
    """
    Runs all 11 supply intelligence layers and consolidates results.
    """

    LAYERS: dict[int, str] = {
        1:  "Trade & Customs Data",
        2:  "B2B Industrial Directories",
        3:  "Govt & Regulatory Databases",
        4:  "Technical Documents",
        5:  "Patents",
        6:  "Trade Show & Exhibitions",
        7:  "LinkedIn & Associate Directories",
        8:  "Export Promotion Councils",
        9:  "Alibaba",
        10: "IndiaMART",
        11: "Specialized Reports",
    }

    def __init__(
        self,
        mode: str = "offline",
        anthropic_api_key: str = None,
    ):
        self.mode = mode
        self.api_key = anthropic_api_key
        self._search = None  # lazy-init ddgs per call
        self._extractor: Optional[object] = SpecExtractor() if _EXTRACTOR_OK else None
        self._claude = None

    # ── LLM helper (API mode only) ────────────────────────────────────────────

    def _llm_extract(self, text: str, material: str) -> list[dict]:
        if self.mode != "api" or not self.api_key:
            return []
        try:
            if self._claude is None:
                import anthropic
                self._claude = anthropic.Anthropic(api_key=self.api_key)
            msg = self._claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Extract supplier/manufacturer info for material '{material}' "
                        f"from the text below.\n"
                        "Return a JSON array. Each object: supplier_name, website, "
                        "region, certifications (list), price_indication, lead_time.\n"
                        "Only real companies. No explanations.\n\n"
                        f"{text[:2500]}"
                    ),
                }],
            )
            raw = msg.content[0].text.strip()
            s = raw.find("[")
            e = raw.rfind("]") + 1
            if s >= 0 and e > s:
                return json.loads(raw[s:e])
        except Exception:
            pass
        return []

    # ── web-search helper ─────────────────────────────────────────────────────

    def _web_search(self, query: str, limit: int = 6) -> list[dict]:
        if not _SEARCH_OK or _DDGS is None:
            return []
        try:
            with _DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=limit))
            # Normalise to common schema: url, title, snippet, domain
            results = []
            for r in raw:
                url = r.get("href", "")
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.replace("www.", "")
                results.append({
                    "url":     url,
                    "title":   r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "domain":  domain,
                })
            return results
        except Exception:
            return []

    def _search_to_suppliers(
        self,
        query: str,
        layer: int,
        material: str,
        limit: int = 6,
    ) -> list[dict]:
        results = self._web_search(query, limit)
        suppliers: list[dict] = []
        snippets: list[str] = []

        for r in results:
            title = r.get("title", "")
            # Title often: "CompanyName | Products" or "CompanyName - Manufacturer"
            raw_name = title.split("|")[0].split(" - ")[0].split(" – ")[0].strip()
            if len(raw_name) < 3:
                continue

            price_usd = None
            lead_days = None
            if self._extractor and r.get("snippet"):
                try:
                    specs = self._extractor.extract(r["snippet"])
                    price_usd = specs.get("price_usd")
                    lead_days = specs.get("lead_time_days")
                except Exception:
                    pass

            suppliers.append(
                _make_supplier(
                    name=raw_name,
                    website=r.get("url", ""),
                    layer=layer,
                    layer_name=self.LAYERS.get(layer, ""),
                    material=material,
                    price_usd=price_usd,
                    lead_time_days=lead_days,
                    snippet=r.get("snippet", ""),
                    confidence=0.40,
                )
            )
            snippets.append(r.get("snippet", ""))

        # API mode: post-process with LLM for better extraction
        if self.mode == "api" and snippets:
            llm_results = self._llm_extract("\n".join(snippets[:3]), material)
            for lr in llm_results:
                suppliers.append(
                    _make_supplier(
                        name=lr.get("supplier_name", ""),
                        website=lr.get("website", ""),
                        region=lr.get("region", "unknown"),
                        layer=layer,
                        layer_name=self.LAYERS.get(layer, ""),
                        material=material,
                        certifications=lr.get("certifications", []),
                        confidence=0.65,
                    )
                )

        return suppliers

    # ── Layer implementations ─────────────────────────────────────────────────

    def layer_1_trade_customs(self, material: str, hsn: str = None) -> list[dict]:
        queries = [
            f'"{material}" importers exporters suppliers trade data customs',
        ]
        if hsn and hsn != "7300":
            queries.append(f'HS code {hsn} "{material}" exporters suppliers')
        suppliers: list[dict] = []
        for q in queries:
            results = self._search_to_suppliers(q, 1, material, limit=5)
            for r in results:
                r["layer_type"] = "trade_customs"
            suppliers.extend(results)
        return suppliers

    def layer_2_b2b_directories(self, material: str) -> list[dict]:
        suppliers: list[dict] = []
        # ThomasNet dedicated scraper
        if _THOMASNET_OK:
            try:
                scraper = ThomasnetScraper()
                raw = scraper.search(material) or []
                for r in (raw[:6] if isinstance(raw, list) else []):
                    suppliers.append(
                        _make_supplier(
                            name=r.get("name") or r.get("supplier_name") or r.get("company", ""),
                            website=r.get("url") or r.get("website", ""),
                            region=r.get("location") or r.get("region", "USA"),
                            layer=2,
                            layer_name=self.LAYERS[2],
                            material=material,
                            certifications=r.get("certifications", []),
                            confidence=0.75,
                        )
                    )
            except Exception:
                pass

        # Europages / Kompass web search
        q = f'"{material}" manufacturer supplier site:europages.co.uk OR site:kompass.com'
        suppliers.extend(self._search_to_suppliers(q, 2, material, limit=5))
        return suppliers

    def layer_3_govt_regulatory(self, material: str) -> list[dict]:
        q = f'"{material}" certified manufacturer ISO 9001 approved vendor list'
        results = self._search_to_suppliers(q, 3, material, limit=5)
        for r in results:
            r["layer_type"] = "regulatory"
        return results

    def layer_4_technical_docs(self, material: str) -> list[dict]:
        if not _PDF_OK:
            return []
        try:
            harvester = PDFHarvester()
            pdfs = harvester.harvest(material, max_results=3) or []
            suppliers: list[dict] = []
            for pdf in pdfs:
                mfr = pdf.get("manufacturer") or pdf.get("supplier")
                if mfr:
                    suppliers.append(
                        _make_supplier(
                            name=mfr,
                            website=pdf.get("url", ""),
                            layer=4,
                            layer_name=self.LAYERS[4],
                            material=material,
                            confidence=0.55,
                            layer_type="technical_document",
                            spec_sheet_url=pdf.get("url", ""),
                        )
                    )
            return suppliers
        except Exception:
            return []

    def layer_5_patents(self, material: str) -> list[dict]:
        q = f'site:patents.google.com "{material}" assignee manufacturer production'
        results = self._search_to_suppliers(q, 5, material, limit=4)
        for r in results:
            r["layer_type"] = "patent_signal"
        return results

    def layer_6_trade_shows(self, material: str) -> list[dict]:
        q = f'"{material}" exhibitor manufacturer trade fair hannover messe 2024 2025'
        results = self._search_to_suppliers(q, 6, material, limit=4)
        for r in results:
            r["layer_type"] = "trade_show"
        return results

    def layer_7_linkedin(self, material: str) -> list[dict]:
        q = f'site:linkedin.com/company "{material}" manufacturer supplier'
        results = self._search_to_suppliers(q, 7, material, limit=4)
        for r in results:
            r["layer_type"] = "company_profile"
        return results

    def layer_8_export_councils(self, material: str) -> list[dict]:
        q = f'"{material}" exporter manufacturer EEPC FIEO India council'
        results = self._search_to_suppliers(q, 8, material, limit=4)
        for r in results:
            r["region"] = r.get("region", "India")
            r["layer_type"] = "export_council"
        return results

    def layer_9_alibaba(self, material: str) -> list[dict]:
        if _ALIBABA_OK:
            try:
                scraper = AlibabaScraper()
                raw = scraper.search(material) or []
                suppliers: list[dict] = []
                for r in (raw[:8] if isinstance(raw, list) else []):
                    suppliers.append(
                        _make_supplier(
                            name=(r.get("supplier") or r.get("company")
                                  or r.get("name", "")),
                            website=(r.get("url") or r.get("product_url", "")),
                            region=(r.get("location") or r.get("country", "China")),
                            layer=9,
                            layer_name=self.LAYERS[9],
                            material=material,
                            price_usd=r.get("price_usd") or r.get("price"),
                            moq=r.get("moq") or r.get("min_order"),
                            lead_time_days=r.get("lead_time_days"),
                            certifications=r.get("certifications", []),
                            confidence=0.65,
                        )
                    )
                return suppliers
            except Exception:
                pass
        # Fallback
        q = f'site:alibaba.com "{material}" manufacturer supplier price'
        return self._search_to_suppliers(q, 9, material, limit=6)

    def layer_10_indiamart(self, material: str) -> list[dict]:
        if _INDIAMART_OK:
            try:
                scraper = IndiamartScraper()
                raw = scraper.search(material) or []
                suppliers: list[dict] = []
                for r in (raw[:8] if isinstance(raw, list) else []):
                    suppliers.append(
                        _make_supplier(
                            name=(r.get("supplier") or r.get("company")
                                  or r.get("name", "")),
                            website=(r.get("url") or r.get("product_url", "")),
                            region=(r.get("location") or r.get("city", "India")),
                            layer=10,
                            layer_name=self.LAYERS[10],
                            material=material,
                            price_usd=r.get("price_usd") or r.get("price"),
                            moq=r.get("moq"),
                            lead_time_days=r.get("lead_time_days"),
                            confidence=0.60,
                        )
                    )
                return suppliers
            except Exception:
                pass
        q = f'site:indiamart.com "{material}" manufacturer supplier'
        return self._search_to_suppliers(q, 10, material, limit=6)

    def layer_11_specialized_reports(self, material: str) -> list[dict]:
        q = f'"{material}" top manufacturers global suppliers market report 2024'
        results = self._search_to_suppliers(q, 11, material, limit=4)
        for r in results:
            r["layer_type"] = "market_research"
        return results

    # ── orchestrator ──────────────────────────────────────────────────────────

    def gather(
        self,
        material_name: str,
        hsn_code: str = None,
        layers: list[int] = None,
        max_workers: int = 4,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Run all (or selected) layers and return consolidated intelligence.

        Parameters
        ----------
        material_name     : resolved material name from product_identifier
        hsn_code          : HS tariff code — unlocks trade/customs layer
        layers            : which layer numbers to run (default: 1-11)
        max_workers       : parallel thread count
        progress_callback : fn(layer_num, layer_name, status_str)

        Returns
        -------
        {
          "suppliers"  : deduplicated flat list of supplier records,
          "by_layer"   : {layer_num: [records]},
          "stats"      : summary counts,
          "material"   : material_name,
          "hsn_code"   : hsn_code,
          "mode"       : self.mode,
        }
        """
        if layers is None:
            layers = list(range(1, 12))

        _layer_fns: dict[int, Callable] = {
            1:  lambda: self.layer_1_trade_customs(material_name, hsn_code),
            2:  lambda: self.layer_2_b2b_directories(material_name),
            3:  lambda: self.layer_3_govt_regulatory(material_name),
            4:  lambda: self.layer_4_technical_docs(material_name),
            5:  lambda: self.layer_5_patents(material_name),
            6:  lambda: self.layer_6_trade_shows(material_name),
            7:  lambda: self.layer_7_linkedin(material_name),
            8:  lambda: self.layer_8_export_councils(material_name),
            9:  lambda: self.layer_9_alibaba(material_name),
            10: lambda: self.layer_10_indiamart(material_name),
            11: lambda: self.layer_11_specialized_reports(material_name),
        }

        by_layer: dict[int, list] = {}
        all_suppliers: list[dict] = []

        def _run(ln: int):
            fn = _layer_fns.get(ln)
            if fn is None:
                return ln, []
            try:
                if progress_callback:
                    progress_callback(ln, self.LAYERS[ln], "running")
                result = fn()
                if progress_callback:
                    progress_callback(ln, self.LAYERS[ln],
                                      f"done ({len(result)} records)")
                return ln, result
            except Exception as exc:
                if progress_callback:
                    progress_callback(ln, self.LAYERS[ln], f"error: {exc}")
                return ln, []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run, ln): ln for ln in layers}
            for future in as_completed(futures):
                ln, records = future.result()
                by_layer[ln] = records
                all_suppliers.extend(records)

        # Deduplicate by (name, website) key
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for s in all_suppliers:
            key = (
                s.get("supplier_name", "").lower()[:35],
                s.get("website", "").lower()[:50],
            )
            if key[0] and key not in seen:
                seen.add(key)
                deduped.append(s)

        return {
            "suppliers": deduped,
            "by_layer": by_layer,
            "stats": {
                "total_raw": len(all_suppliers),
                "total_deduped": len(deduped),
                "by_layer_count": {ln: len(v) for ln, v in by_layer.items()},
                "layers_run": len(layers),
            },
            "material": material_name,
            "hsn_code": hsn_code,
            "mode": self.mode,
        }
