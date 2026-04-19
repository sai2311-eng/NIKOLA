"""
Rule-Based Query Expander — Zero LLM
Expands a user query like "5mm bolt" into all possible search terms,
standard designations, and synonym variants across component categories.
"""

import re
from typing import Optional

# ─── Component type detection keywords ───────────────────────────────────────
FASTENER_KEYWORDS = [
    "bolt", "screw", "nut", "washer", "rivet", "stud", "anchor",
    "threaded", "hex", "socket", "fastener", "m3", "m4", "m5", "m6", "m8",
    "m10", "m12", "1/4\"", "3/8\"", "5/16\""
]
CAPACITOR_KEYWORDS = ["capacitor", "cap", "mlcc", "electrolytic", "tantalum", "ceramic", "nf", "uf", "pf"]
RESISTOR_KEYWORDS  = ["resistor", "res", "ohm", "kohm", "mohm", "thick film", "thin film"]
IC_KEYWORDS        = ["ic", "chip", "op-amp", "opamp", "microcontroller", "mcu", "mpu", "mosfet", "transistor"]
BEARING_KEYWORDS   = ["bearing", "ball bearing", "roller bearing", "thrust bearing"]
SEAL_KEYWORDS      = ["seal", "o-ring", "oring", "gasket", "washer"]
CONNECTOR_KEYWORDS = ["connector", "plug", "socket", "terminal", "header", "jst", "molex", "dsub"]

# ─── Thread standards mapping ─────────────────────────────────────────────────
METRIC_THREAD_MAP = {
    "1mm":  {"iso": "M1",   "din": "M1",   "pitch_coarse": 0.25, "pitch_fine": None},
    "1.6mm":{"iso": "M1.6", "din": "M1.6", "pitch_coarse": 0.35, "pitch_fine": None},
    "2mm":  {"iso": "M2",   "din": "M2",   "pitch_coarse": 0.40, "pitch_fine": None},
    "2.5mm":{"iso": "M2.5", "din": "M2.5", "pitch_coarse": 0.45, "pitch_fine": None},
    "3mm":  {"iso": "M3",   "din": "M3",   "pitch_coarse": 0.50, "pitch_fine": None},
    "4mm":  {"iso": "M4",   "din": "M4",   "pitch_coarse": 0.70, "pitch_fine": 0.5},
    "5mm":  {"iso": "M5",   "din": "M5",   "pitch_coarse": 0.80, "pitch_fine": 0.5},
    "6mm":  {"iso": "M6",   "din": "M6",   "pitch_coarse": 1.00, "pitch_fine": 0.75},
    "8mm":  {"iso": "M8",   "din": "M8",   "pitch_coarse": 1.25, "pitch_fine": 1.0},
    "10mm": {"iso": "M10",  "din": "M10",  "pitch_coarse": 1.50, "pitch_fine": 1.25},
    "12mm": {"iso": "M12",  "din": "M12",  "pitch_coarse": 1.75, "pitch_fine": 1.5},
}

# UNC/UNF imperial equivalents (closest practical substitutes)
IMPERIAL_EQUIVALENTS = {
    "M3":  ["4-40 UNC", "6-32 UNC", "#4 screw"],
    "M4":  ["8-32 UNC", "10-32 UNF"],
    "M5":  ["10-24 UNC", "10-32 UNF", "#10 screw", '3/16" screw'],
    "M6":  ["1/4-20 UNC", "1/4-28 UNF"],
    "M8":  ["5/16-18 UNC", "5/16-24 UNF"],
    "M10": ["3/8-16 UNC", "3/8-24 UNF"],
    "M12": ["1/2-13 UNC", "1/2-20 UNF"],
}

# DIN standard numbers for common bolt types
DIN_STANDARDS = {
    "hex_bolt":          ["DIN 931", "DIN 933", "ISO 4014", "ISO 4017"],
    "socket_head_cap":   ["DIN 912", "ISO 4762"],
    "button_head":       ["DIN 7380", "ISO 7380"],
    "flat_head":         ["DIN 7991", "ISO 10642"],
    "pan_head":          ["DIN 7985", "ISO 7045"],
    "set_screw":         ["DIN 913", "DIN 914", "ISO 4026"],
    "hex_nut":           ["DIN 934", "ISO 4032"],
    "washer":            ["DIN 125", "DIN 127", "ISO 7089"],
}

# Material variants for fasteners
FASTENER_MATERIALS = [
    "stainless steel", "A2 stainless", "A4 stainless", "316 stainless", "304 stainless",
    "carbon steel", "alloy steel", "grade 8.8", "grade 10.9", "grade 12.9",
    "zinc plated", "hot-dip galvanized", "black oxide", "nylon", "brass",
    "titanium", "aluminum", "inconel"
]

# Capacitance range for component type detection
CAPACITANCE_PATTERN = re.compile(r'(\d+(?:\.\d+)?)\s*(p[Ff]|n[Ff]|[uµ][Ff]|m[Ff])', re.IGNORECASE)
RESISTANCE_PATTERN  = re.compile(r'(\d+(?:\.\d+)?)\s*([kKmM]?\s*[Oo]hm|[kKmM]?\s*[Oo]|[kKmM]?\u03a9)', re.IGNORECASE)
SIZE_MM_PATTERN     = re.compile(r'(\d+(?:\.\d+)?)\s*mm', re.IGNORECASE)
SIZE_INCH_PATTERN   = re.compile(r'(\d+(?:\.\d+)?)\s*(?:inch|in|")', re.IGNORECASE)


def detect_component_type(query: str) -> str:
    """Detect component type from query string."""
    q = query.lower()
    for kw in FASTENER_KEYWORDS:
        if kw in q:
            return "fastener"
    for kw in CAPACITOR_KEYWORDS:
        if kw in q:
            return "capacitor"
    for kw in RESISTOR_KEYWORDS:
        if kw in q:
            return "resistor"
    for kw in IC_KEYWORDS:
        if kw in q:
            return "ic"
    for kw in BEARING_KEYWORDS:
        if kw in q:
            return "bearing"
    for kw in CONNECTOR_KEYWORDS:
        if kw in q:
            return "connector"

    # Size-based detection
    mm_match = SIZE_MM_PATTERN.search(query)
    if mm_match:
        size = float(mm_match.group(1))
        if 1 <= size <= 100:
            return "fastener"  # likely a mechanical part

    return "unknown"


def expand_query(query: str, component_type: Optional[str] = None) -> dict:
    """
    Expand a component query into all possible search variants.

    Returns:
        {
          "original": query,
          "type": detected_type,
          "primary_terms": [...],     # best search terms
          "variant_terms": [...],     # synonyms / alternate designations
          "standard_designations": [...],  # ISO/DIN/ANSI codes
          "material_variants": [...], # material-specific searches
          "search_queries": [...],    # ready-to-use search strings
          "metadata": {...}           # parsed dimensions etc.
        }
    """
    ctype = component_type or detect_component_type(query)

    result = {
        "original": query,
        "type": ctype,
        "primary_terms": [],
        "variant_terms": [],
        "standard_designations": [],
        "material_variants": [],
        "search_queries": [],
        "metadata": {}
    }

    if ctype == "fastener":
        _expand_fastener(query, result)
    elif ctype == "capacitor":
        _expand_capacitor(query, result)
    elif ctype == "resistor":
        _expand_resistor(query, result)
    elif ctype == "ic":
        _expand_ic(query, result)
    elif ctype == "bearing":
        _expand_bearing(query, result)
    else:
        _expand_generic(query, result)

    # Build final search query list (deduplicated)
    all_queries = set()
    component_context = {
        "fastener": "industrial fastener buy price",
        "capacitor": "electronics component buy price",
        "resistor":  "electronics component buy",
        "ic":        "semiconductor buy datasheet",
        "bearing":   "bearing buy price supplier",
    }.get(result.get("type", "unknown"), "buy price supplier")

    for term in result["primary_terms"]:
        all_queries.add(f"{term} {component_context}")
        all_queries.add(f"{term} site:grainger.com OR site:mcmaster.com OR site:rs-online.com OR site:fastenal.com")
        all_queries.add(f"{term} datasheet specifications")
    for term in result["standard_designations"][:3]:
        all_queries.add(f"{term} {component_context}")
    for mat in result["material_variants"][:2]:
        if result["primary_terms"]:
            all_queries.add(f"{result['primary_terms'][0]} {mat} buy")

    result["search_queries"] = list(all_queries)
    return result


def _expand_fastener(query: str, result: dict):
    q = query.lower().strip()

    # Extract size
    mm_match = SIZE_MM_PATTERN.search(q)
    inch_match = SIZE_INCH_PATTERN.search(q)

    size_mm = None
    size_key = None

    # Check for explicit M-designation first (M5, M6, etc.)
    m_desig = re.search(r'\b(M(\d+(?:\.\d+)?))\b', q, re.IGNORECASE)
    if m_desig:
        size_mm = float(m_desig.group(2))
        size_key = f"{int(size_mm) if size_mm == int(size_mm) else size_mm}mm"
        result["metadata"]["size_mm"] = size_mm
    elif mm_match:
        size_mm = float(mm_match.group(1))
        size_key = f"{int(size_mm) if size_mm == int(size_mm) else size_mm}mm"
        result["metadata"]["size_mm"] = size_mm
    elif inch_match:
        size_in = float(inch_match.group(1))
        size_mm = round(size_in * 25.4, 2)
        size_key = f"{int(size_mm) if size_mm == int(size_mm) else size_mm}mm"
        result["metadata"]["size_mm"] = size_mm
        result["metadata"]["size_inch"] = size_in

    # Determine bolt type from query
    bolt_type = "hex_bolt"  # default
    if "socket" in q or "cap" in q or "allen" in q:
        bolt_type = "socket_head_cap"
    elif "button" in q:
        bolt_type = "button_head"
    elif "flat" in q or "countersunk" in q:
        bolt_type = "flat_head"
    elif "pan" in q:
        bolt_type = "pan_head"
    elif "set" in q or "grub" in q:
        bolt_type = "set_screw"
    elif "nut" in q:
        bolt_type = "hex_nut"
    elif "washer" in q:
        bolt_type = "washer"
    result["metadata"]["bolt_type"] = bolt_type

    # ISO/metric designation
    iso_desig = None
    if size_key and size_key in METRIC_THREAD_MAP:
        info = METRIC_THREAD_MAP[size_key]
        iso_desig = info["iso"]
        result["metadata"]["iso_designation"] = iso_desig
        result["metadata"]["pitch_coarse"] = info["pitch_coarse"]

        # Standard designations
        result["standard_designations"].extend(DIN_STANDARDS.get(bolt_type, []))
        if iso_desig:
            # Add full thread call-out
            pitch = info["pitch_coarse"]
            result["standard_designations"].append(f"{iso_desig}x{pitch}")
            result["standard_designations"].append(iso_desig)
            result["primary_terms"].append(f"{iso_desig} {bolt_type.replace('_', ' ')}")

        # Imperial equivalents
        if iso_desig in IMPERIAL_EQUIVALENTS:
            result["variant_terms"].extend(IMPERIAL_EQUIVALENTS[iso_desig])
            result["metadata"]["imperial_equivalents"] = IMPERIAL_EQUIVALENTS[iso_desig]

    # Build primary terms
    bolt_noun = "bolt" if "bolt" in q else ("screw" if "screw" in q else ("nut" if "nut" in q else "bolt"))
    type_adj = bolt_type.replace("_", " ").replace("hex bolt", "hex head bolt")

    if iso_desig:
        result["primary_terms"].extend([
            f"{iso_desig} {bolt_noun}",
            f"{iso_desig} {type_adj}",
            f"{size_key} bolt",
            f"{size_key} {bolt_noun}",
        ])
    elif size_mm:
        result["primary_terms"].extend([
            f"{size_mm}mm {bolt_noun}",
            f"{size_mm}mm {type_adj}",
        ])
    else:
        result["primary_terms"].append(q)

    # Variant terms (synonyms)
    result["variant_terms"].extend([
        f"fastener {iso_desig or size_key}",
        f"machine {bolt_noun} {iso_desig or ''}".strip(),
        f"threaded {bolt_noun} {iso_desig or ''}".strip(),
    ])
    if bolt_type == "socket_head_cap":
        result["variant_terms"].extend(["SHCS", "socket cap", "allen bolt", "hex socket bolt"])
    if bolt_type == "hex_bolt":
        result["variant_terms"].extend(["hex head bolt", "hex cap screw"])

    # Materials
    result["material_variants"] = FASTENER_MATERIALS.copy()


def _expand_capacitor(query: str, result: dict):
    cap_match = CAPACITANCE_PATTERN.search(query)
    if cap_match:
        value = float(cap_match.group(1))
        unit  = cap_match.group(2).lower()
        result["metadata"]["capacitance_raw"] = f"{value}{unit}"

        # Normalize to nF
        nf_value = value
        if "pf" in unit:
            nf_value = value / 1000
        elif "uf" in unit or "µf" in unit or "mf" in unit.replace("mf", "µf"):
            nf_value = value * 1000
        result["metadata"]["capacitance_nf"] = nf_value

    # Voltage
    v_match = re.search(r'(\d+)\s*[Vv]', query)
    if v_match:
        result["metadata"]["voltage_v"] = int(v_match.group(1))

    # Dielectric
    for diel in ["X7R", "X5R", "C0G", "NP0", "Y5V", "Z5U"]:
        if diel.lower() in query.lower():
            result["metadata"]["dielectric"] = diel

    # Package
    for pkg in ["0201", "0402", "0603", "0805", "1206", "1210", "1812"]:
        if pkg in query:
            result["metadata"]["package"] = pkg

    cap_str = result["metadata"].get("capacitance_raw", query)
    volt_str = f"{result['metadata'].get('voltage_v', '')}V" if result["metadata"].get("voltage_v") else ""
    pkg_str  = result["metadata"].get("package", "")
    diel_str = result["metadata"].get("dielectric", "")

    result["primary_terms"] = [
        f"capacitor {cap_str} {volt_str}".strip(),
        f"MLCC {cap_str} {volt_str} {pkg_str}".strip(),
        f"ceramic capacitor {cap_str}",
    ]
    result["variant_terms"] = [
        f"SMD capacitor {cap_str}",
        f"chip capacitor {cap_str}",
        f"{diel_str} capacitor" if diel_str else "",
    ]
    result["standard_designations"] = ["IEC 60384", "EIA-198"]
    result["material_variants"] = ["ceramic", "electrolytic", "tantalum", "film"]


def _expand_resistor(query: str, result: dict):
    res_match = RESISTANCE_PATTERN.search(query)
    if res_match:
        value = float(res_match.group(1))
        unit  = res_match.group(2).lower().strip()
        multiplier = 1000 if "k" in unit else (1e6 if "m" in unit else 1)
        result["metadata"]["resistance_ohm"] = value * multiplier

    for pkg in ["0201", "0402", "0603", "0805", "1206"]:
        if pkg in query:
            result["metadata"]["package"] = pkg

    r_str = res_match.group(0) if res_match else query
    result["primary_terms"] = [
        f"resistor {r_str}",
        f"chip resistor {r_str}",
        f"SMD resistor {r_str}",
    ]
    result["variant_terms"] = ["thick film resistor", "thin film resistor", "precision resistor"]
    result["standard_designations"] = ["IEC 60115", "AEC-Q200"]
    result["material_variants"] = ["thick film", "thin film", "wirewound", "metal oxide"]


def _expand_ic(query: str, result: dict):
    result["primary_terms"] = [query, f"{query} IC", f"{query} chip"]
    result["variant_terms"] = [f"{query} datasheet", f"{query} equivalent", f"{query} substitute"]
    result["standard_designations"] = []
    result["material_variants"] = []


def _expand_bearing(query: str, result: dict):
    # Extract bearing number pattern (e.g., 6205, 608)
    bearing_match = re.search(r'\b(\d{3,4})\b', query)
    if bearing_match:
        num = bearing_match.group(1)
        result["metadata"]["bearing_number"] = num
        result["primary_terms"] = [
            f"{num} bearing", f"deep groove ball bearing {num}",
            f"SKF {num}", f"NSK {num}", f"FAG {num}",
        ]
        result["standard_designations"] = [f"ISO {num}", f"DIN 625", f"JIS B 1521"]
    else:
        result["primary_terms"] = [query, f"{query} bearing"]
    result["material_variants"] = [
        "open bearing", "sealed bearing (2RS)", "shielded bearing (ZZ)",
        "stainless steel bearing"
    ]


def _expand_generic(query: str, result: dict):
    result["primary_terms"] = [query, f"{query} supplier", f"{query} buy", f"{query} price"]
    result["variant_terms"] = [f"{query} datasheet", f"{query} equivalent"]
    result["standard_designations"] = []
    result["material_variants"] = []


if __name__ == "__main__":
    tests = ["5mm bolt", "M6 socket head cap screw", "100nF capacitor", "1k resistor 0402", "6205 bearing"]
    for t in tests:
        r = expand_query(t)
        print(f"\nQuery: '{t}'  Type: {r['type']}")
        print(f"  Primary: {r['primary_terms'][:3]}")
        print(f"  Standards: {r['standard_designations'][:4]}")
        print(f"  Metadata: {r['metadata']}")
