"""
PDF Harvester — Zero LLM
Finds, downloads, and extracts data from spec sheet PDFs on the web.
Uses search engines to discover PDFs, then pdfplumber to extract text + tables.
"""

import re
import os
import hashlib
import time
import random
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

DOWNLOAD_DIR = Path(__file__).parent.parent / "data" / "spec_sheets"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
]


def find_datasheets(query: str, max_results: int = 5) -> list[dict]:
    """
    Search for PDF spec sheets for a component.
    Returns list of {url, title, filename}.
    """
    pdf_links = []

    # Search DuckDuckGo for PDFs
    pdf_query = f"{query} datasheet filetype:pdf"
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(pdf_query, max_results=max_results * 2))
        for r in results:
            url = r.get("href", "")
            if url.lower().endswith(".pdf") or "pdf" in url.lower():
                pdf_links.append({
                    "url": url,
                    "title": r.get("title", ""),
                    "source": "duckduckgo_pdf_search"
                })
            if len(pdf_links) >= max_results:
                break
    except Exception:
        pass

    # Also search for spec sheets without filetype filter
    if len(pdf_links) < max_results:
        spec_query = f"{query} specification sheet PDF"
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(spec_query, max_results=10))
            for r in results:
                url = r.get("href", "")
                if url.lower().endswith(".pdf"):
                    pdf_links.append({
                        "url": url,
                        "title": r.get("title", ""),
                        "source": "duckduckgo_spec_search"
                    })
        except Exception:
            pass

    # Deduplicate by URL
    seen = set()
    unique = []
    for p in pdf_links:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)
    return unique[:max_results]


def download_pdf(url: str, filename: Optional[str] = None) -> Optional[Path]:
    """
    Download a PDF to the spec_sheets directory.
    Returns local path or None on failure.
    """
    try:
        import requests
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        resp = requests.get(url, headers=headers, timeout=20, stream=True)
        if resp.status_code != 200:
            return None

        # Content-type check
        ct = resp.headers.get("content-type", "")
        if "pdf" not in ct.lower() and not url.lower().endswith(".pdf"):
            # Peek at content
            first_bytes = next(resp.iter_content(chunk_size=512), b"")
            if not first_bytes.startswith(b"%PDF"):
                return None
            # It is a PDF, reassemble
            content = first_bytes + b"".join(resp.iter_content(chunk_size=8192))
        else:
            content = b"".join(resp.iter_content(chunk_size=8192))

        if not filename:
            # Generate filename from URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            url_name = urlparse(url).path.split("/")[-1]
            filename = url_name if url_name.endswith(".pdf") else f"spec_{url_hash}.pdf"

        path = DOWNLOAD_DIR / filename
        path.write_bytes(content)
        return path

    except Exception as e:
        return None


def extract_from_pdf(pdf_path: str) -> dict:
    """
    Extract ALL data from a PDF spec sheet — zero LLM.
    Extracts: raw text, tables, and parsed fields.
    """
    result = {
        "source_file": str(pdf_path),
        "raw_text": "",
        "tables": [],
        "pages": 0,
        "extracted_fields": {}
    }

    try:
        import pdfplumber
    except ImportError:
        result["error"] = "pdfplumber not installed"
        return result

    try:
        with pdfplumber.open(pdf_path) as pdf:
            result["pages"] = len(pdf.pages)
            all_text = []
            all_tables = []

            for page in pdf.pages:
                # Extract text
                text = page.extract_text()
                if text:
                    all_text.append(text)

                # Extract tables (crucial for spec sheets)
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        # Convert table rows to dicts
                        cleaned = []
                        for row in table:
                            if row and any(cell for cell in row if cell):
                                cleaned.append([str(c or "").strip() for c in row])
                        if cleaned:
                            all_tables.append(cleaned)

            result["raw_text"] = "\n".join(all_text)
            result["tables"] = all_tables[:20]  # cap

            # Parse all extracted text and tables
            result["extracted_fields"] = _parse_all_fields(result["raw_text"], all_tables)

    except Exception as e:
        result["error"] = str(e)

    return result


def _parse_all_fields(text: str, tables: list) -> dict:
    """
    Master field parser — extracts ALL recognizable fields from PDF text + tables.
    Zero LLM. Uses domain-specific regex patterns.
    """
    fields = {}

    # ── Part identification ────────────────────────────────────────────────────
    # Part number
    pn_match = re.search(
        r'(?:part\s*(?:number|no\.?|#)|p/n|item\s*(?:no|number|#)|model\s*(?:no|number)?)'
        r'[:\s]+([A-Z0-9][A-Z0-9\-/\.]{2,30})',
        text, re.IGNORECASE
    )
    if pn_match:
        fields["part_number"] = pn_match.group(1).strip()

    # Manufacturer
    mfr_match = re.search(
        r'(?:manufacturer|brand|made\s*by|produced\s*by)[:\s]+([A-Za-z][A-Za-z\s,\.&]{2,50})',
        text, re.IGNORECASE
    )
    if mfr_match:
        fields["manufacturer"] = mfr_match.group(1).strip().rstrip(",.")[:50]

    # ── Fastener/mechanical specs ─────────────────────────────────────────────
    # Thread designation (M5, M6x1, etc.)
    thread_match = re.search(
        r'\b(M\d+(?:\.\d+)?(?:\s*[xX×]\s*\d+(?:\.\d+)?)?)\b',
        text
    )
    if thread_match:
        fields["thread"] = thread_match.group(1).replace(" ", "")

    # Nominal diameter
    dia_match = re.search(
        r'(?:nominal\s*dia(?:meter)?|thread\s*size|bolt\s*size)[:\s]+(\d+(?:\.\d+)?)\s*(mm|in)',
        text, re.IGNORECASE
    )
    if dia_match:
        fields["nominal_diameter"] = f"{dia_match.group(1)}{dia_match.group(2)}"

    # Thread pitch
    pitch_match = re.search(
        r'(?:pitch|thread\s*pitch)[:\s]+(\d+(?:\.\d+)?)\s*mm',
        text, re.IGNORECASE
    )
    if pitch_match:
        fields["thread_pitch_mm"] = float(pitch_match.group(1))

    # Length
    length_match = re.search(
        r'(?:length|bolt\s*length|grip\s*length)[:\s]+(\d+(?:\.\d+)?)\s*(mm|in)',
        text, re.IGNORECASE
    )
    if length_match:
        fields["length"] = f"{length_match.group(1)}{length_match.group(2)}"

    # Head type
    head_match = re.search(
        r'(?:head\s*type|head\s*style|drive\s*type)[:\s]+'
        r'(hex(?:agon)?|socket|pan|flat|button|round|oval|countersunk|fillister)',
        text, re.IGNORECASE
    )
    if head_match:
        fields["head_type"] = head_match.group(1).lower()

    # ── Material ───────────────────────────────────────────────────────────────
    mat_match = re.search(
        r'(?:material|made\s*of|base\s*material)[:\s]+'
        r'(stainless\s*steel|carbon\s*steel|alloy\s*steel|brass|aluminum|nylon|titanium|'
        r'A2\s*stainless|A4\s*stainless|316\s*SS|304\s*SS|[A-Z0-9]{2,10}\s*steel)',
        text, re.IGNORECASE
    )
    if mat_match:
        fields["material"] = mat_match.group(1).strip()

    # ── Strength / Grade ────────────────────────────────────────────────────────
    grade_match = re.search(
        r'(?:property\s*class|grade|strength\s*class)[:\s]+'
        r'(\d+\.\d+|\d+G|Grade\s*\d+|A2|A4|A1|B8|[5-9]\.\d)',
        text, re.IGNORECASE
    )
    if grade_match:
        fields["grade"] = grade_match.group(1).strip()

    # Tensile strength
    tensile_match = re.search(
        r'(?:tensile\s*strength|ultimate\s*strength|proof\s*load)[:\s]+'
        r'(\d+(?:\.\d+)?)\s*(?:MPa|N/mm2|ksi|psi)',
        text, re.IGNORECASE
    )
    if tensile_match:
        fields["tensile_strength"] = tensile_match.group(0).strip()

    # ── Electronic component specs ────────────────────────────────────────────
    # Capacitance
    cap_match = re.search(
        r'(\d+(?:\.\d+)?)\s*(pF|nF|[uµμ]F|mF)',
        text, re.IGNORECASE
    )
    if cap_match:
        fields["capacitance"] = f"{cap_match.group(1)}{cap_match.group(2)}"

    # Resistance
    res_match = re.search(
        r'(\d+(?:\.\d+)?)\s*([kKmM]?[ΩOo]hm|[kKmM]\s*Ω)',
        text, re.IGNORECASE
    )
    if res_match:
        fields["resistance"] = res_match.group(0).strip()

    # Voltage rating
    volt_match = re.search(
        r'(?:rated\s*voltage|voltage\s*rating|working\s*voltage|WVDC)[:\s]*(\d+(?:\.\d+)?)\s*[Vv]',
        text
    )
    if volt_match:
        fields["voltage_rating"] = f"{volt_match.group(1)}V"

    # ── Compliance / Standards ─────────────────────────────────────────────────
    compliance = []
    standard_patterns = {
        "RoHS": r"RoHS\s*(?:3|2\.0|compliant|III|2011/65/EU)?",
        "REACH": r"REACH\s*(?:compliant|regulation|SVHC)?",
        "CE": r"\bCE\s*(?:marked|marking|certified)?\b",
        "ISO 9001": r"ISO\s*9001",
        "ISO 4762": r"ISO\s*4762",
        "DIN 912": r"DIN\s*912",
        "DIN 931": r"DIN\s*931",
        "DIN 933": r"DIN\s*933",
        "ASTM A193": r"ASTM\s*A193",
        "ASTM A307": r"ASTM\s*A307",
        "ANSI B18": r"ANSI\s*B18",
        "AEC-Q200": r"AEC[-–]?Q200",
        "AEC-Q100": r"AEC[-–]?Q100",
    }
    for cert, pattern in standard_patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            compliance.append(cert)
    if compliance:
        fields["compliance"] = compliance

    # ── Pricing ────────────────────────────────────────────────────────────────
    price_match = re.search(r'(?:unit\s*price|price\s*per\s*(?:piece|unit|pc))[:\s]*[\$€£]?\s*(\d+\.\d+)', text, re.I)
    if price_match:
        fields["unit_price"] = float(price_match.group(1))

    # ── Temperature range ──────────────────────────────────────────────────────
    temp_match = re.search(
        r'(-\s*\d+)\s*°?[Cc]\s*(?:to|~|–|-)\s*\+?\s*(\d+)\s*°?[Cc]',
        text
    )
    if temp_match:
        fields["temperature_range"] = f"{temp_match.group(1).strip()}°C to +{temp_match.group(2)}°C"

    # ── Surface treatment ──────────────────────────────────────────────────────
    finish_match = re.search(
        r'(?:surface\s*(?:treatment|finish|coating)|finish|plating)[:\s]+'
        r'(zinc\s*plated|hot.dip\s*galvanized|black\s*oxide|nickel\s*plated|'
        r'chrome\s*plated|plain|passivated|dacromet)',
        text, re.IGNORECASE
    )
    if finish_match:
        fields["surface_finish"] = finish_match.group(1).strip()

    # ── From tables ────────────────────────────────────────────────────────────
    table_fields = _extract_from_tables(tables)
    for k, v in table_fields.items():
        if k not in fields:
            fields[k] = v

    return fields


def _extract_from_tables(tables: list) -> dict:
    """Extract key-value pairs from spec tables."""
    fields = {}
    KEY_MAP = {
        "material": "material",
        "grade": "grade",
        "finish": "surface_finish",
        "surface": "surface_finish",
        "thread": "thread",
        "pitch": "thread_pitch_mm",
        "length": "length",
        "diameter": "nominal_diameter",
        "tensile": "tensile_strength",
        "proof": "proof_load",
        "hardness": "hardness",
        "standards": "standard",
        "standard": "standard",
        "weight": "weight_per_piece",
        "rohs": "rohs_compliant",
        "voltage": "voltage_rating",
        "capacitance": "capacitance",
        "resistance": "resistance",
        "tolerance": "tolerance",
    }
    for table in tables:
        for row in table:
            if len(row) >= 2 and row[0] and row[1]:
                key = row[0].lower().strip().rstrip(":")
                val = row[1].strip()
                if not key or not val or len(key) > 50:
                    continue
                for kw, canonical in KEY_MAP.items():
                    if kw in key:
                        fields[canonical] = val
                        break
    return fields


def harvest_datasheets(query: str, max_pdfs: int = 3) -> list[dict]:
    """
    Full pipeline: search → download → extract for a given query.
    Returns list of extracted data dicts.
    """
    results = []
    pdf_links = find_datasheets(query, max_results=max_pdfs)

    for link in pdf_links:
        url = link["url"]
        filename = f"{query.replace(' ', '_')[:30]}_{hashlib.md5(url.encode()).hexdigest()[:6]}.pdf"
        local_path = download_pdf(url, filename)

        entry = {
            "source_url": url,
            "title": link.get("title", ""),
            "local_path": str(local_path) if local_path else None,
            "extracted": {}
        }

        if local_path and local_path.exists():
            entry["extracted"] = extract_from_pdf(str(local_path))

        results.append(entry)
        time.sleep(random.uniform(0.5, 1.0))

    return results


import hashlib  # needed at module top level


if __name__ == "__main__":
    import json, sys
    q = sys.argv[1] if len(sys.argv) > 1 else "M5 stainless steel bolt DIN 912"
    print(f"Harvesting datasheets for: {q}")
    results = harvest_datasheets(q, max_pdfs=2)
    for r in results:
        print(f"\nURL: {r['source_url'][:80]}")
        print(f"Fields: {list(r['extracted'].get('extracted_fields', {}).keys())}")
