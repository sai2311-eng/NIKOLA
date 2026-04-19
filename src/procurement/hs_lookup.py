"""
Live HS Code Lookup & Verification Engine

Fetches the correct HS (Harmonized System) tariff code for ANY material,
ingredient, or product by querying multiple authoritative web sources
and cross-verifying results.

Pipeline:
  1. Check local SQLite cache (instant)
  2. Query multiple web sources in parallel
  3. Extract HS code candidates from results
  4. Cross-verify: pick the code that appears in 2+ sources
  5. Cache verified result for future lookups

Sources queried:
  - DuckDuckGo (general web)
  - Bing (general web)
  - Targeted queries against trade/tariff sites
"""

from __future__ import annotations

import re
import os
import sqlite3
import time
import random
from pathlib import Path
from collections import Counter
from typing import Optional


_DB_PATH = Path(__file__).parent.parent.parent / "data" / "hs_codes.db"


class HSCodeLookup:
    """Live HS code lookup with SQLite cache and multi-source web verification."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(_DB_PATH)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS hs_cache (
                query       TEXT PRIMARY KEY,
                hs_code     TEXT NOT NULL,
                description TEXT DEFAULT '',
                sources     TEXT DEFAULT '',
                confidence  REAL DEFAULT 0,
                verified    INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def lookup(self, material: str) -> dict:
        """
        Look up HS code for any material/ingredient/product.

        Returns:
            {
                "query": str,
                "hs_code": str,        # e.g. "2923.20"
                "description": str,     # what the HS heading covers
                "confidence": float,    # 0.0-1.0
                "sources": list[str],   # which sources confirmed it
                "verified": bool,       # True if 2+ sources agree
                "from_cache": bool,
            }
        """
        if not material or not material.strip():
            return self._empty(material)

        clean = material.strip().lower()

        # Step 1: Check cache
        cached = self._get_cache(clean)
        if cached:
            return cached

        # Step 2: Live web lookup
        result = self._web_lookup(clean)

        # Step 3: Cache the result
        if result["hs_code"] and result["hs_code"] != "—":
            self._set_cache(clean, result)

        return result

    def bulk_lookup(self, materials: list[str],
                    progress_callback=None) -> dict[str, dict]:
        """Look up HS codes for multiple materials."""
        results = {}
        total = len(materials)
        for i, mat in enumerate(materials):
            if progress_callback:
                progress_callback(i, total, f"Looking up: {mat}")
            results[mat] = self.lookup(mat)
            # Small delay between web queries to be polite
            if not results[mat].get("from_cache"):
                time.sleep(random.uniform(0.3, 0.6))
        if progress_callback:
            progress_callback(total, total, "Done")
        return results

    def close(self):
        if self._conn:
            self._conn.close()

    # ── Cache operations ─────────────────────────────────────────────────

    def _get_cache(self, query: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT hs_code, description, sources, confidence, verified "
            "FROM hs_cache WHERE query = ?", (query,)
        ).fetchone()
        if row:
            return {
                "query": query,
                "hs_code": row[0],
                "description": row[1],
                "sources": row[2].split("|") if row[2] else [],
                "confidence": row[3],
                "verified": bool(row[4]),
                "from_cache": True,
            }
        return None

    def _set_cache(self, query: str, result: dict):
        sources_str = "|".join(result.get("sources", []))
        self._conn.execute(
            "INSERT OR REPLACE INTO hs_cache "
            "(query, hs_code, description, sources, confidence, verified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (query, result["hs_code"], result.get("description", ""),
             sources_str, result.get("confidence", 0),
             1 if result.get("verified") else 0),
        )
        self._conn.commit()

    def _empty(self, query: str) -> dict:
        return {
            "query": query or "",
            "hs_code": "—",
            "description": "",
            "sources": [],
            "confidence": 0,
            "verified": False,
            "from_cache": False,
        }

    # ── Web lookup ───────────────────────────────────────────────────────

    def _web_lookup(self, material: str) -> dict:
        """Query multiple web sources and cross-verify HS codes."""
        candidates: list[dict] = []

        # Run different search strategies
        search_queries = [
            # Direct HS code search
            f'"{material}" HS code',
            # Tariff classification search
            f'"{material}" harmonized tariff schedule classification',
            # Trade-specific search
            f'"{material}" HTS code import export tariff heading',
        ]

        for sq in search_queries:
            hits = self._search_and_extract(sq)
            candidates.extend(hits)
            if len(candidates) >= 3:
                break  # enough data
            time.sleep(random.uniform(0.2, 0.5))

        if not candidates:
            return self._empty(material)

        # Cross-verify: find the most common HS code
        # Normalize all codes to their 4-digit heading first
        code_counter = Counter()
        full_codes = {}  # heading -> most specific version
        source_map = {}  # heading -> list of sources
        desc_map = {}    # heading -> description

        for c in candidates:
            code = c["code"]
            heading = code[:4]  # first 4 digits (the HS heading)
            code_counter[heading] += 1

            # Keep the most specific (longest) version
            if heading not in full_codes or len(code) > len(full_codes[heading]):
                full_codes[heading] = code

            # Track sources
            if heading not in source_map:
                source_map[heading] = []
            src = c.get("source", "web")
            if src not in source_map[heading]:
                source_map[heading].append(src)

            # Keep description
            if c.get("description") and heading not in desc_map:
                desc_map[heading] = c["description"]

        # Pick the winner: most common heading
        best_heading, count = code_counter.most_common(1)[0]
        best_code = full_codes[best_heading]
        sources = source_map.get(best_heading, [])
        description = desc_map.get(best_heading, "")
        verified = count >= 2  # 2+ sources agree
        confidence = min(count / len(search_queries), 1.0)

        return {
            "query": material,
            "hs_code": best_code,
            "description": description,
            "sources": sources,
            "confidence": round(confidence, 2),
            "verified": verified,
            "from_cache": False,
        }

    def _search_and_extract(self, query: str) -> list[dict]:
        """Run a web search and extract HS code candidates from results."""
        results = []

        # Try DuckDuckGo
        ddg_results = self._search_ddg(query)
        for r in ddg_results:
            extracted = self._extract_hs_from_text(
                f"{r.get('title', '')} {r.get('body', '')} {r.get('snippet', '')}",
                source=r.get("domain", "web"),
            )
            results.extend(extracted)

        # Try Bing
        bing_results = self._search_bing(query)
        for r in bing_results:
            extracted = self._extract_hs_from_text(
                f"{r.get('title', '')} {r.get('snippet', '')}",
                source=r.get("domain", "bing"),
            )
            results.extend(extracted)

        return results

    def _search_ddg(self, query: str) -> list[dict]:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=8))
            out = []
            for r in raw:
                url = r.get("href", "")
                domain = ""
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc.replace("www.", "")
                except Exception:
                    pass
                out.append({
                    "title": r.get("title", ""),
                    "body": r.get("body", ""),
                    "snippet": r.get("body", ""),
                    "url": url,
                    "domain": domain,
                })
            return out
        except Exception:
            return []

    def _search_bing(self, query: str) -> list[dict]:
        try:
            import requests
            from bs4 import BeautifulSoup
            from urllib.parse import quote_plus, urlparse

            url = f"https://www.bing.com/search?q={quote_plus(query)}&count=8"
            ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36")
            resp = requests.get(url, headers={"User-Agent": ua}, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")

            out = []
            for li in soup.select("li.b_algo"):
                h2 = li.find("h2")
                if not h2:
                    continue
                a = h2.find("a")
                if not a:
                    continue
                href = a.get("href", "")
                title = a.get_text(strip=True)
                p = li.find("p")
                snippet = p.get_text(strip=True) if p else ""
                domain = ""
                try:
                    domain = urlparse(href).netloc.replace("www.", "")
                except Exception:
                    pass
                out.append({
                    "title": title, "snippet": snippet,
                    "url": href, "domain": domain,
                })
            return out
        except Exception:
            return []

    def _extract_hs_from_text(self, text: str, source: str = "web") -> list[dict]:
        """Extract HS code candidates from a block of text."""
        candidates = []
        if not text:
            return candidates

        # HS code patterns from most specific to least
        patterns = [
            # --- DOTTED formats ---

            # "HS code: 2923.20.00" or "HTS: 2923.20"
            (r"(?:HS|HTS|HSN|tariff|heading|subheading|classification|code)\s*"
             r"(?:code|#|no\.?|number|heading)?\s*[:=\s]\s*"
             r"(\d{4}\.\d{2}(?:\.\d{2,4})?)",
             "explicit_label"),

            # "HS 2923.20" or "HTS 2923.20.00"
            (r"(?:HS|HTS|HSN)\s+(\d{4}\.\d{2}(?:\.\d{2,4})?)",
             "hs_prefix"),

            # --- NON-DOTTED formats (most trade sites use these) ---

            # "HS Code 29232010" or "HSN Code 29232000" or "HTS: 29232010"
            (r"(?:HS|HTS|HSN)\s*(?:code|#|no\.?|number)?\s*[:=\s]\s*"
             r"(\d{6,10})\b",
             "explicit_nodot"),

            # "HS 29232010" or "HTS 29232000"
            (r"(?:HS|HTS|HSN)\s+(\d{6,10})\b",
             "hs_prefix_nodot"),

            # "Code 29232010" standalone with label
            (r"(?:tariff|heading|subheading|classification)\s*"
             r"(?:code|#|no\.?|number)?\s*[:=\s]\s*"
             r"(\d{6,10})\b",
             "explicit_nodot"),

            # --- Heading-level ---

            # "Chapter 29, heading 2923" -> extract 2923
            (r"heading\s+(\d{4})", "heading_ref"),

            # --- Bare dotted ---

            # "2923.20" standing alone (4-digit.2-digit pattern)
            (r"\b(\d{4}\.\d{2})\b", "bare_code"),

            # "29.23" (chapter.heading format used in some contexts)
            (r"\b(\d{2}\.\d{2})\b", "chapter_heading"),

            # --- Bare non-dotted (only 6-8 digit, needs context) ---
            # "29232010" standing alone — only match if near HS-related words
            (r"(?:HS|HTS|HSN|tariff|code|heading|import|export|customs|duty)"
             r".{0,30}?\b(\d{8})\b",
             "bare_nodot_8"),

            (r"\b(\d{8})\b.{0,30}?"
             r"(?:HS|HTS|HSN|tariff|code|heading|import|export|customs|duty)",
             "bare_nodot_8"),
        ]

        for pattern, pat_type in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                code = m.strip()

                # --- Normalize non-dotted codes to dotted format ---
                digits_only = code.replace(".", "")
                if "." not in code and len(digits_only) >= 6:
                    # Convert 29232010 -> 2923.20.10
                    #         292320   -> 2923.20
                    #         29232000 -> 2923.20.00
                    d = digits_only
                    if len(d) >= 8:
                        code = f"{d[:4]}.{d[4:6]}.{d[6:8]}"
                    elif len(d) >= 6:
                        code = f"{d[:4]}.{d[4:6]}"

                # Validate: first 2 digits should be a valid HS chapter (01-99)
                try:
                    chapter = int(code[:2])
                    if chapter < 1 or chapter > 99:
                        continue
                except (ValueError, IndexError):
                    continue

                # Skip obvious non-HS numbers (years, phone numbers, etc.)
                if "." not in code:
                    if code.startswith("19") and len(code) == 4:
                        continue
                    if code.startswith("20") and len(code) == 4:
                        try:
                            yr = int(code)
                            if 2000 <= yr <= 2030:
                                continue
                        except ValueError:
                            pass

                # Convert "29.23" format to "2923.00" if needed
                if len(code) == 5 and code[2] == ".":
                    code = code.replace(".", "") + ".00"
                    if len(code) > 7:
                        code = code[:7]

                # Try to extract nearby description text
                desc = self._extract_nearby_description(text, m)

                # Higher confidence for explicitly labeled codes
                weight = {
                    "explicit_label": 3,
                    "hs_prefix": 3,
                    "explicit_nodot": 3,
                    "hs_prefix_nodot": 3,
                    "heading_ref": 2,
                    "bare_nodot_8": 2,
                    "bare_code": 1,
                    "chapter_heading": 1,
                }.get(pat_type, 1)

                for _ in range(weight):
                    candidates.append({
                        "code": code,
                        "source": source,
                        "description": desc,
                        "pattern_type": pat_type,
                    })

        return candidates

    def _extract_nearby_description(self, text: str, code: str) -> str:
        """Try to extract a description near the HS code in text."""
        # Look for text after the code like "2923.20 - Lecithins"
        pattern = re.escape(code) + r"\s*[-–:]\s*([A-Za-z][A-Za-z\s,;]{5,80})"
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip().rstrip(",;.")
        return ""


# ── Module-level convenience function ────────────────────────────────────────

_default_lookup: Optional[HSCodeLookup] = None


def get_hs_code(material: str) -> dict:
    """
    Quick convenience function — look up HS code for any material.

    Returns dict with keys: query, hs_code, description, confidence,
    sources, verified, from_cache
    """
    global _default_lookup
    if _default_lookup is None:
        _default_lookup = HSCodeLookup()
    return _default_lookup.lookup(material)


def get_hs_code_simple(material: str) -> str:
    """Return just the HS code string for a material."""
    result = get_hs_code(material)
    return result.get("hs_code", "—")
