"""
Barcode / UPC / EAN Lookup Engine.

Two input paths:
  1. Manual barcode entry  -> number
  2. Photo upload          -> OCR / pyzbar decode -> number

Lookup chain (tries each until product found):
  1. Open Food Facts API   (world.openfoodfacts.org — free, no key)
  2. UPC Food Search       (upcfoodsearch.com — scrape)
  3. Barcode Lookup         (barcodelookup.com — scrape)

Extracts:
  - Product name
  - Product price
  - Product ingredients list
  - GTIN (Global Trade Item Number)
  - HS / tariff code (inferred from product category)
"""

from __future__ import annotations

import io
import re
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── HS Code Live Lookup ──────────────────────────────────────────────────────
# Uses src.procurement.hs_lookup for live web-verified HS codes.
# Kept a small seed map for instant offline fallback only.
_SEED_HS_MAP = {
    # ── Lecithins & emulsifiers ──
    "soy lecithin":          "2923.20",
    "soya lecithin":         "2923.20",
    "sunflower lecithin":    "2923.20",
    "lecithin":              "2923.20",
    "polysorbate 80":        "3402.13",
    "polysorbate 20":        "3402.13",
    "mono and diglycerides": "3823.19",
    "monoglyceride":         "3823.19",
    "diglyceride":           "3823.19",
    "sodium stearoyl lactylate": "2918.19",
    # ── Organic acids ──
    "citric acid":           "2918.14",
    "malic acid":            "2918.19",
    "tartaric acid":         "2918.12",
    "lactic acid":           "2918.11",
    "fumaric acid":          "2917.19",
    "acetic acid":           "2915.21",
    "ascorbic acid":         "2936.27",
    "sorbic acid":           "2916.19",
    "benzoic acid":          "2916.31",
    "salicylic acid":        "2918.21",
    "stearic acid":          "2915.70",
    "oleic acid":            "2916.15",
    "palmitic acid":         "2915.70",
    "adipic acid":           "2917.12",
    "succinic acid":         "2917.19",
    "gluconic acid":         "2918.16",
    "propionic acid":        "2915.31",
    "butyric acid":          "2915.60",
    "formic acid":           "2915.11",
    "oxalic acid":           "2917.11",
    "cinnamic acid":         "2916.39",
    "phosphoric acid":       "2809.20",
    "hydrochloric acid":     "2806.10",
    "sulfuric acid":         "2807.00",
    "nitric acid":           "2808.00",
    # ── Salts & preservatives ──
    "sodium benzoate":       "2916.31",
    "potassium sorbate":     "2916.19",
    "sodium citrate":        "2918.15",
    "calcium citrate":       "2918.15",
    "sodium chloride":       "2501.00",
    "potassium chloride":    "3104.20",
    "calcium chloride":      "2827.20",
    "sodium bicarbonate":    "2836.30",
    "sodium carbonate":      "2836.20",
    "calcium carbonate":     "2836.50",
    "magnesium carbonate":   "2836.99",
    "sodium phosphate":      "2835.22",
    "calcium phosphate":     "2835.26",
    "sodium sulfate":        "2833.11",
    "sodium nitrate":        "3102.50",
    "sodium nitrite":        "2834.10",
    "sodium metabisulfite":  "2832.10",
    "sodium erythorbate":    "2932.20",
    "sodium acetate":        "2915.29",
    "potassium carbonate":   "2836.40",
    # ── Stearates (excipients) ──
    "magnesium stearate":    "2915.70",
    "calcium stearate":      "2915.70",
    "zinc stearate":         "2915.70",
    # ── Sweeteners ──
    "sucrose":               "1701.99",
    "glucose":               "1702.30",
    "fructose":              "1702.60",
    "dextrose":              "1702.30",
    "maltodextrin":          "1702.90",
    "high fructose corn syrup": "1702.60",
    "corn syrup":            "1702.30",
    "sorbitol":              "2905.44",
    "mannitol":              "2905.43",
    "xylitol":               "2905.49",
    "erythritol":            "2905.49",
    "aspartame":             "2924.29",
    "sucralose":             "2932.99",
    "stevia":                "2938.90",
    "saccharin":             "2925.11",
    "acesulfame":            "2935.90",
    "maltitol":              "2940.00",
    "isomalt":               "2940.00",
    "trehalose":             "2940.00",
    "inulin":                "1702.90",
    # ── Gums & thickeners ──
    "xanthan gum":           "3913.90",
    "guar gum":              "1302.32",
    "carrageenan":           "1302.39",
    "pectin":                "1302.20",
    "gum arabic":            "1301.20",
    "acacia gum":            "1301.20",
    "locust bean gum":       "1302.32",
    "agar":                  "1302.31",
    "gellan gum":            "1302.39",
    "konjac":                "1302.39",
    "carboxymethyl cellulose": "3912.31",
    "cmc":                   "3912.31",
    "methylcellulose":       "3912.39",
    "hydroxypropyl methylcellulose": "3912.39",
    "hpmc":                  "3912.39",
    # ── Starches ──
    "corn starch":           "1108.12",
    "cornstarch":            "1108.12",
    "potato starch":         "1108.13",
    "tapioca starch":        "1108.14",
    "wheat starch":          "1108.11",
    "rice starch":           "1108.19",
    "modified corn starch":  "3505.10",
    "modified starch":       "3505.10",
    "pregelatinized starch": "3505.10",
    "starch":                "1108.19",
    # ── Cellulose ──
    "microcrystalline cellulose": "3912.90",
    "cellulose":             "3912.90",
    "croscarmellose sodium": "3912.31",
    # ── Proteins ──
    "whey protein":          "0404.10",
    "whey":                  "0404.10",
    "casein":                "3501.10",
    "caseinate":             "3501.90",
    "sodium caseinate":      "3501.90",
    "gelatin":               "3503.00",
    "collagen":              "3504.00",
    "soy protein":           "2106.10",
    "pea protein":           "2106.10",
    "gluten":                "1109.00",
    "wheat gluten":          "1109.00",
    "albumin":               "3502.20",
    # ── Fats & oils ──
    "palm oil":              "1511.10",
    "coconut oil":           "1513.11",
    "soybean oil":           "1507.10",
    "sunflower oil":         "1512.11",
    "rapeseed oil":          "1514.11",
    "canola oil":            "1514.11",
    "olive oil":             "1509.10",
    "corn oil":              "1515.21",
    "cottonseed oil":        "1512.21",
    "sesame oil":            "1515.50",
    "castor oil":            "1515.30",
    "linseed oil":           "1515.11",
    "flaxseed oil":          "1515.11",
    "fish oil":              "1504.20",
    "shea butter":           "1515.90",
    "cocoa butter":          "1804.00",
    "mct oil":               "1516.20",
    "glycerin":              "1520.00",
    "glycerol":              "1520.00",
    # ── Minerals & oxides ──
    "silicon dioxide":       "2811.22",
    "titanium dioxide":      "2823.00",
    "zinc oxide":            "2817.00",
    "magnesium oxide":       "2519.90",
    "iron oxide":            "2821.10",
    "calcium oxide":         "2522.10",
    "aluminum oxide":        "2818.10",
    "talc":                  "2526.20",
    "kaolin":                "2507.00",
    "mica":                  "2525.10",
    "calcium sulfate":       "2520.10",
    "magnesium sulfate":     "2530.20",
    "ferrous sulfate":       "2833.29",
    "ferrous fumarate":      "2917.19",
    "zinc sulfate":          "2833.29",
    "copper sulfate":        "2833.25",
    "manganese sulfate":     "2833.29",
    "chromium picolinate":   "2933.39",
    "selenium":              "2804.90",
    # ── Vitamins ──
    "vitamin a":             "2936.21",
    "retinol":               "2936.21",
    "vitamin b1":            "2936.22",
    "thiamine":              "2936.22",
    "vitamin b2":            "2936.23",
    "riboflavin":            "2936.23",
    "vitamin b3":            "2936.29",
    "niacin":                "2936.29",
    "niacinamide":           "2936.29",
    "vitamin b5":            "2936.24",
    "pantothenic acid":      "2936.24",
    "vitamin b6":            "2936.25",
    "pyridoxine":            "2936.25",
    "vitamin b7":            "2936.29",
    "biotin":                "2936.29",
    "vitamin b9":            "2936.26",
    "folic acid":            "2936.26",
    "folate":                "2936.26",
    "vitamin b12":           "2936.26",
    "cyanocobalamin":        "2936.26",
    "vitamin c":             "2936.27",
    "vitamin d":             "2936.28",
    "cholecalciferol":       "2936.28",
    "ergocalciferol":        "2936.28",
    "vitamin d3":            "2936.28",
    "vitamin e":             "2936.28",
    "tocopherol":            "2936.28",
    "alpha tocopherol":      "2936.28",
    "vitamin k":             "2936.29",
    "phytonadione":          "2936.29",
    # ── Amino acids ──
    "l-lysine":              "2922.41",
    "lysine":                "2922.41",
    "l-glutamic acid":       "2922.42",
    "glutamic acid":         "2922.42",
    "msg":                   "2922.42",
    "monosodium glutamate":  "2922.42",
    "glycine":               "2922.49",
    "l-tryptophan":          "2922.50",
    "tryptophan":            "2922.50",
    "l-methionine":          "2930.40",
    "methionine":            "2930.40",
    "l-cysteine":            "2930.90",
    "cysteine":              "2930.90",
    "taurine":               "2922.50",
    "creatine":              "2925.29",
    # ── Alkaloids & plant extracts ──
    "caffeine":              "2939.30",
    "menthol":               "2906.11",
    "camphor":               "2914.21",
    "vanillin":              "2912.41",
    "capsaicin":             "2939.99",
    "curcumin":              "3203.00",
    "quercetin":             "2938.90",
    "resveratrol":           "2932.99",
    "coenzyme q10":          "2914.69",
    "lutein":                "3203.00",
    "beta carotene":         "3204.19",
    "lycopene":              "3203.00",
    "chlorophyll":           "3203.00",
    # ── Alcohols & solvents ──
    "ethanol":               "2207.10",
    "methanol":              "2905.11",
    "isopropanol":           "2905.12",
    "propylene glycol":      "2905.32",
    "butylene glycol":       "2905.39",
    "polyethylene glycol":   "3907.20",
    "peg":                   "3907.20",
    "acetone":               "2914.11",
    "ethyl acetate":         "2915.31",
    "benzyl alcohol":        "2906.21",
    # ── Colorants ──
    "carmine":               "3203.00",
    "annatto":               "3203.00",
    "caramel color":         "1702.90",
    "paprika oleoresin":     "3301.90",
    "spirulina extract":     "1302.19",
    "beet juice":            "2009.89",
    "fd&c":                  "3204.19",
    "red 40":                "3204.19",
    "yellow 5":              "3204.19",
    "yellow 6":              "3204.19",
    "blue 1":                "3204.19",
    "blue 2":                "3204.19",
    # ── Flavors & essential oils ──
    "essential oil":         "3301.29",
    "natural flavor":        "3302.10",
    "artificial flavor":     "3302.90",
    "vanilla extract":       "1302.19",
    "peppermint oil":        "3301.25",
    "eucalyptus oil":        "3301.29",
    "lavender oil":          "3301.29",
    "lemon oil":             "3301.13",
    "orange oil":            "3301.12",
    "tea tree oil":          "3301.29",
    # ── Polymers & plastics ──
    "polyethylene":          "3901.10",
    "polypropylene":         "3902.10",
    "polystyrene":           "3903.11",
    "pvc":                   "3904.10",
    "pet":                   "3907.61",
    "polyester":             "3907.91",
    "nylon":                 "3908.10",
    "polycarbonate":         "3907.40",
    "abs":                   "3903.30",
    "silicone":              "3910.00",
    "polyurethane":          "3909.50",
    # ── Packaging materials ──
    "pet bottle":            "3923.30",
    "glass bottle":          "7010.90",
    "aluminum can":          "7612.90",
    "cardboard":             "4819.10",
    "plastic film":          "3920.10",
    "shrink wrap":           "3920.10",
    # ── Misc industrial ──
    "sodium hydroxide":      "2815.11",
    "potassium hydroxide":   "2815.20",
    "hydrogen peroxide":     "2847.00",
    "calcium hypochlorite":  "2828.10",
    "sodium hypochlorite":   "2828.90",
    "urea":                  "3102.10",
    "ammonia":               "2814.10",
    "activated carbon":      "3802.10",
    "diatomaceous earth":    "2512.00",
    "bentonite":             "2508.10",
    "beeswax":               "1521.90",
    "paraffin wax":          "2712.20",
    "shellac":               "1301.90",
}

# Broader product category fallback (used when no specific ingredient matches)
_CATEGORY_HS_MAP = {
    # Food & beverages
    "beverages": "2202", "drinks": "2202", "water": "2201", "juice": "2009",
    "coffee": "0901", "tea": "0902", "milk": "0401", "dairy": "0406",
    "cheese": "0406", "yogurt": "0403", "chocolate": "1806", "candy": "1704",
    "snack": "1905", "cereal": "1904", "bread": "1905", "pasta": "1902",
    "rice": "1006", "flour": "1101", "sugar": "1701", "oil": "1509",
    "sauce": "2103", "condiment": "2103", "spice": "0910", "meat": "0201",
    "fish": "0302", "seafood": "0306", "fruit": "0810", "vegetable": "0709",
    "frozen": "0710", "canned": "2005", "soup": "2104", "baby food": "1901",
    "pet food": "2309", "honey": "0409", "butter": "0405", "cream": "0401",
    "egg": "0407", "nut": "0802", "seed": "1207", "herb": "1211",
    "vinegar": "2209", "jam": "2007", "ice cream": "2105",
    # Supplements & health
    "supplement": "2106", "vitamin": "2936", "mineral": "2833",
    "protein": "0404", "nutrition": "2106", "health": "2106",
    "medicine": "3004", "pharmaceutical": "3004", "capsule": "3926.90",
    "tablet": "3004", "probiotic": "3002.90",
    # Personal care
    "shampoo": "3305", "soap": "3401", "cosmetic": "3304", "skincare": "3304",
    "toothpaste": "3306", "deodorant": "3307", "sunscreen": "3304",
    "moisturizer": "3304", "lip balm": "3304", "perfume": "3303",
    "hair dye": "3305", "nail polish": "3304",
    # Household
    "detergent": "3402", "cleaner": "3402", "tissue": "4818", "paper": "4818",
    "disinfectant": "3808", "insecticide": "3808", "fertilizer": "3105",
}

# Cache for web-verified HS codes
_hs_code_cache: dict[str, str] = {}


def _infer_hs_code(product_name: str, categories: str = "") -> str:
    """
    Look up the HS code for any material/ingredient/product.

    Pipeline:
      1. Live web lookup via hs_lookup module (queries DuckDuckGo + Bing,
         cross-verifies from 2+ sources, caches in SQLite)
      2. Offline seed map fallback (for when web is unavailable)
      3. Broad category fallback
    """
    text = (product_name + " " + categories).lower().strip()

    # Step 1: Live lookup (uses SQLite cache internally, so instant for repeats)
    try:
        from src.procurement.hs_lookup import get_hs_code
        result = get_hs_code(product_name)
        code = result.get("hs_code", "")
        if code and code != "—":
            return code
    except Exception:
        pass  # fall through to offline seed map

    # Step 2: Offline seed map (fast local fallback)
    for keyword in sorted(_SEED_HS_MAP.keys(), key=len, reverse=True):
        if keyword in text:
            return _SEED_HS_MAP[keyword]

    # Step 3: Broad category fallback
    for keyword in sorted(_CATEGORY_HS_MAP.keys(), key=len, reverse=True):
        if keyword in text:
            return _CATEGORY_HS_MAP[keyword]

    return "—"


def _normalise_barcode(raw: str) -> str:
    """Strip whitespace and non-digit characters from barcode."""
    return re.sub(r"[^0-9]", "", raw.strip())


def _barcode_to_gtin(barcode: str) -> str:
    """Pad barcode to GTIN-14 format."""
    b = _normalise_barcode(barcode)
    if len(b) <= 14:
        return b.zfill(14)
    return b[:14]


def _gtin_check_digit(body: str) -> int:
    """Return the GS1 check digit for a GTIN body."""
    total = 0
    reversed_digits = list(reversed(body))
    for idx, ch in enumerate(reversed_digits, start=1):
        digit = int(ch)
        total += digit * (3 if idx % 2 == 1 else 1)
    return (10 - (total % 10)) % 10


def _is_valid_gtin(code: str) -> bool:
    """Validate EAN-8 / UPC-A / EAN-13 / GTIN-14 using GS1 checksum."""
    norm = _normalise_barcode(code)
    if len(norm) not in {8, 12, 13, 14} or not norm.isdigit():
        return False
    expected = _gtin_check_digit(norm[:-1])
    return expected == int(norm[-1])


def _barcode_priority(code: str) -> tuple[int, int]:
    """Rank likely retail barcodes ahead of weaker OCR candidates."""
    norm = _normalise_barcode(code)
    valid = 1 if _is_valid_gtin(norm) else 0
    preferred_len = 1 if len(norm) in {12, 13} else 0
    return (valid, preferred_len)


def _extract_barcode_like_sequences(text: str) -> list[str]:
    """Extract contiguous or grouped digit sequences likely to be retail barcodes."""
    candidates: list[str] = []

    # Direct contiguous matches.
    candidates.extend(re.findall(r"\b\d{8,14}\b", text))

    # OCR often preserves the printed grouping under EAN/UPC barcodes, e.g.
    # "4105250 022003" or "4 105250 022003". Capture short digit groups that
    # together form a valid 8/12/13/14-digit barcode once separators are removed.
    grouped_matches = re.findall(r"(?:\d[\d\s-]{6,20}\d)", text)
    for match in grouped_matches:
        compact = _normalise_barcode(match)
        if len(compact) in {8, 12, 13, 14}:
            candidates.append(compact)

    return candidates


def _unique_codes(codes: list[str]) -> list[str]:
    """Keep plausible barcode candidates in best-first order."""
    collected: list[str] = []
    seen: set[str] = set()
    for code in codes:
        norm = _normalise_barcode(code)
        if not norm or not norm.isdigit() or norm in seen:
            continue
        # Restrict image extraction to the retail barcode lengths we support.
        if len(norm) not in {8, 12, 13, 14}:
            continue
        seen.add(norm)
        collected.append(norm)

    valid = [c for c in collected if _is_valid_gtin(c)]
    if valid:
        return sorted(valid, key=_barcode_priority, reverse=True)
    return sorted(collected, key=_barcode_priority, reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE → BARCODE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def decode_barcode_from_image(image_bytes: bytes) -> list[str]:
    """
    Extract barcode numbers from an image using pyzbar + OpenCV.
    Returns list of decoded barcode strings.
    """
    results: list[str] = []

    # Try PIL-first decoding with EXIF orientation handling.
    try:
        from pyzbar import pyzbar
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)

        pil_variants = [
            img,
            img.convert("L"),
        ]

        for variant in pil_variants:
            decoded = pyzbar.decode(variant)
            for d in decoded:
                code = d.data.decode("utf-8", errors="ignore").strip()
                if code:
                    results.append(code)
        if results:
            return _unique_codes(results)
    except Exception as e:
        logger.warning(f"pyzbar decode failed: {e}")

    # Fallback: OpenCV pipeline tuned for camera captures where the barcode
    # occupies a small region of a larger frame.
    try:
        import cv2
        import numpy as np
        from pyzbar import pyzbar
        from PIL import Image, ImageOps

        pil = Image.open(io.BytesIO(image_bytes))
        pil = ImageOps.exif_transpose(pil).convert("RGB")
        arr = np.array(pil)
        img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        if img is None:
            return results

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        def _candidate_regions(frame: np.ndarray) -> list[np.ndarray]:
            h, w = frame.shape[:2]
            regions: list[np.ndarray] = [frame]

            # Common phone-camera failure mode: barcode is centered but small.
            # Add progressively tighter center crops.
            crop_specs = [
                (0.85, 0.65),
                (0.75, 0.50),
                (0.60, 0.35),
            ]
            for wf, hf in crop_specs:
                cw = max(1, int(w * wf))
                ch = max(1, int(h * hf))
                x1 = max(0, (w - cw) // 2)
                y1 = max(0, (h - ch) // 2)
                regions.append(frame[y1:y1 + ch, x1:x1 + cw])

            # Barcodes are often horizontal bands in the lower/central part.
            band_specs = [
                (0.20, 0.80),
                (0.30, 0.70),
                (0.40, 0.65),
            ]
            for top_f, bot_f in band_specs:
                y1 = max(0, int(h * top_f))
                y2 = min(h, int(h * bot_f))
                if y2 > y1:
                    regions.append(frame[y1:y2, :])

            return regions

        def _preprocess_variants(frame: np.ndarray) -> list[np.ndarray]:
            local_gray = frame if len(frame.shape) == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(local_gray)
            variants = [
                local_gray,
                clahe,
                cv2.GaussianBlur(local_gray, (3, 3), 0),
                cv2.medianBlur(local_gray, 3),
                cv2.threshold(local_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
                cv2.adaptiveThreshold(local_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 31, 5),
                cv2.Sobel(local_gray, cv2.CV_8U, 1, 0, ksize=3),
            ]

            # Enlarge smaller regions; phone shots often need zooming before decode.
            scaled: list[np.ndarray] = []
            for variant in variants:
                h, w = variant.shape[:2]
                if max(h, w) < 1600:
                    scaled.append(cv2.resize(variant, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC))
                if max(h, w) < 900:
                    scaled.append(cv2.resize(variant, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC))
            variants.extend(scaled)
            return variants

        for region in _candidate_regions(img):
            for angle in (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
                working = region if angle is None else cv2.rotate(region, angle)
                for proc in _preprocess_variants(working):
                    decoded = pyzbar.decode(proc)
                    for d in decoded:
                        code = d.data.decode("utf-8", errors="ignore").strip()
                        if code:
                            results.append(code)
                    if results:
                        return _unique_codes(results)
    except Exception as e:
        logger.warning(f"OpenCV barcode fallback failed: {e}")

    return _unique_codes(results)


def ocr_barcode_from_image(image_bytes: bytes) -> list[str]:
    """
    Use OCR (Tesseract) to find barcode numbers in an image.
    Fallback when pyzbar can't decode the barcode directly.
    """
    results = []
    try:
        import pytesseract
        from PIL import Image, ImageOps
        import cv2
        import numpy as np

        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img).convert("L")
        arr = np.array(img)

        ocr_inputs = [
            arr,
            cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY, 31, 5),
        ]

        texts: list[str] = []
        for candidate in ocr_inputs:
            pil_candidate = Image.fromarray(candidate)
            texts.append(pytesseract.image_to_string(
                pil_candidate,
                config="--psm 6 -c tessedit_char_whitelist=0123456789"
            ))
            texts.append(pytesseract.image_to_string(
                pil_candidate,
                config="--psm 11 -c tessedit_char_whitelist=0123456789"
            ))

        for text in texts:
            matches = _extract_barcode_like_sequences(text)
            results.extend(matches)
    except Exception as e:
        logger.warning(f"OCR barcode extraction failed: {e}")

    return _unique_codes(results)


def extract_barcode_from_image(image_bytes: bytes) -> Optional[str]:
    """
    Try barcode decode, then OCR, return first valid barcode or None.
    """
    codes = decode_barcode_from_image(image_bytes)
    if not codes:
        codes = ocr_barcode_from_image(image_bytes)
    return codes[0] if codes else None


# ══════════════════════════════════════════════════════════════════════════════
# LOOKUP SOURCES
# ══════════════════════════════════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": "SAI-ProcurementIntelligence/1.0 (research; contact@sai.dev)"
}


def lookup_openfoodfacts(barcode: str) -> Optional[dict]:
    """
    Query Open Food Facts API (free, no API key required).
    Returns product dict or None.
    """
    barcode = _normalise_barcode(barcode)
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") != 1:
            return None

        product = data.get("product", {})
        name = (
            product.get("product_name")
            or product.get("product_name_en")
            or product.get("generic_name")
            or ""
        )

        # Extract ingredients
        ingredients_text = product.get("ingredients_text") or product.get("ingredients_text_en") or ""
        ingredients_list = []
        if product.get("ingredients"):
            ingredients_list = [
                ing.get("text", ing.get("id", "")).strip()
                for ing in product["ingredients"]
                if ing.get("text") or ing.get("id")
            ]

        # Categories
        categories = product.get("categories") or product.get("categories_tags_en") or ""
        if isinstance(categories, list):
            categories = ", ".join(categories)

        # Brands
        brands = product.get("brands") or ""

        return {
            "source": "Open Food Facts",
            "barcode": barcode,
            "gtin": _barcode_to_gtin(barcode),
            "product_name": name,
            "brand": brands,
            "price": None,  # OFF doesn't have pricing
            "ingredients_text": ingredients_text,
            "ingredients_list": ingredients_list or [
                i.strip() for i in ingredients_text.split(",") if i.strip()
            ],
            "categories": categories,
            "hs_code": _infer_hs_code(name, categories),
            "image_url": product.get("image_url") or product.get("image_front_url"),
            "nutrition_grade": product.get("nutrition_grades"),
            "nutriscore": product.get("nutriscore_grade"),
            "quantity": product.get("quantity"),
            "packaging": product.get("packaging"),
            "countries": product.get("countries"),
            "raw_data": {
                k: product.get(k)
                for k in ["nova_group", "ecoscore_grade", "labels", "stores",
                          "manufacturing_places", "origins"]
                if product.get(k)
            },
        }
    except Exception as e:
        logger.warning(f"Open Food Facts lookup failed for {barcode}: {e}")
        return None


def lookup_barcodelookup(barcode: str) -> Optional[dict]:
    """
    Scrape barcodelookup.com for product info.
    Returns product dict or None.
    """
    barcode = _normalise_barcode(barcode)
    url = f"https://www.barcodelookup.com/{barcode}"

    try:
        resp = requests.get(url, headers={
            **_HEADERS,
            "Accept": "text/html,application/xhtml+xml",
        }, timeout=10)
        if resp.status_code != 200:
            return None

        html = resp.text

        # Extract product name from <h4> or <title>
        name_match = re.search(r'<h4[^>]*class="product-name"[^>]*>([^<]+)</h4>', html)
        if not name_match:
            name_match = re.search(r"<title>([^<|]+)", html)
        name = name_match.group(1).strip() if name_match else ""
        # Clean up title
        name = re.sub(r"\s*[-|]\s*Barcode.*$", "", name).strip()

        if not name or "not found" in name.lower():
            return None

        # Extract price
        price = None
        price_match = re.search(r'\$[\d,.]+', html)
        if price_match:
            try:
                price = float(re.sub(r"[^\d.]", "", price_match.group()))
            except ValueError:
                pass

        # Extract description / ingredients
        desc = ""
        desc_match = re.search(
            r'<span[^>]*class="product-text"[^>]*>(.*?)</span>',
            html, re.DOTALL
        )
        if desc_match:
            desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()

        # Try to find ingredients
        ingredients_text = ""
        ing_match = re.search(
            r'(?:ingredients|Ingredients)[:\s]*(.*?)(?:\.|<)',
            html, re.DOTALL | re.IGNORECASE
        )
        if ing_match:
            ingredients_text = re.sub(r"<[^>]+>", "", ing_match.group(1)).strip()

        # Category
        cat_match = re.search(r'<span[^>]*>Category:</span>\s*([^<]+)', html)
        category = cat_match.group(1).strip() if cat_match else ""

        return {
            "source": "Barcode Lookup",
            "barcode": barcode,
            "gtin": _barcode_to_gtin(barcode),
            "product_name": name,
            "brand": "",
            "price": price,
            "ingredients_text": ingredients_text or desc,
            "ingredients_list": [
                i.strip() for i in (ingredients_text or desc).split(",") if i.strip()
            ],
            "categories": category,
            "hs_code": _infer_hs_code(name, category),
            "image_url": None,
            "raw_data": {},
        }
    except Exception as e:
        logger.warning(f"Barcode Lookup scrape failed for {barcode}: {e}")
        return None


def lookup_upcfoodsearch(barcode: str) -> Optional[dict]:
    """
    Scrape upcfoodsearch.com for product info.
    Returns product dict or None.
    """
    barcode = _normalise_barcode(barcode)
    url = f"https://www.upcfoodsearch.com/upc/{barcode}/"

    try:
        resp = requests.get(url, headers={
            **_HEADERS,
            "Accept": "text/html,application/xhtml+xml",
        }, timeout=10)
        if resp.status_code != 200:
            return None

        html = resp.text

        # Extract product name
        name_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        name = name_match.group(1).strip() if name_match else ""

        if not name or "not found" in name.lower() or "error" in name.lower():
            return None

        # Extract ingredients
        ingredients_text = ""
        ing_match = re.search(
            r'(?:Ingredients|INGREDIENTS)[:\s]*(.*?)(?:</p>|</div>|\n\n)',
            html, re.DOTALL | re.IGNORECASE
        )
        if ing_match:
            ingredients_text = re.sub(r"<[^>]+>", "", ing_match.group(1)).strip()

        # Brand
        brand = ""
        brand_match = re.search(r'(?:Brand|Manufacturer)[:\s]*([^<\n]+)', html, re.IGNORECASE)
        if brand_match:
            brand = brand_match.group(1).strip()

        return {
            "source": "UPC Food Search",
            "barcode": barcode,
            "gtin": _barcode_to_gtin(barcode),
            "product_name": name,
            "brand": brand,
            "price": None,
            "ingredients_text": ingredients_text,
            "ingredients_list": [
                i.strip() for i in ingredients_text.split(",") if i.strip()
            ],
            "categories": "",
            "hs_code": _infer_hs_code(name),
            "image_url": None,
            "raw_data": {},
        }
    except Exception as e:
        logger.warning(f"UPC Food Search scrape failed for {barcode}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL OPEN FOOD FACTS DATABASE (for offline / downloaded data)
# ══════════════════════════════════════════════════════════════════════════════

_OFF_LOCAL_DIR = Path(__file__).resolve().parents[2] / "data" / "openfoodfacts"
_OFF_INDEX_DB = _OFF_LOCAL_DIR / "off_index.db"

# Singleton connection for the local OFF SQLite index
_off_conn: Optional[sqlite3.Connection] = None


def _get_off_conn() -> Optional[sqlite3.Connection]:
    """Get (or create) a connection to the local OFF index database."""
    global _off_conn
    if _off_conn is not None:
        return _off_conn
    if not _OFF_INDEX_DB.exists():
        return None
    try:
        _off_conn = sqlite3.connect(str(_OFF_INDEX_DB), check_same_thread=False)
        _off_conn.row_factory = sqlite3.Row
        return _off_conn
    except Exception as e:
        logger.warning(f"Failed to open OFF index DB: {e}")
        return None


def lookup_openfoodfacts_local(barcode: str) -> Optional[dict]:
    """
    Look up barcode in the local Open Food Facts SQLite index.
    The index is built from the downloaded CSV by build_off_index.py
    (4.4M products, instant lookup via PRIMARY KEY).
    """
    barcode = _normalise_barcode(barcode)

    conn = _get_off_conn()
    if conn is None:
        return None

    try:
        row = conn.execute(
            "SELECT * FROM products WHERE code = ?", (barcode,)
        ).fetchone()
    except sqlite3.ProgrammingError:
        # Reconnect if called from a different thread
        global _off_conn
        _off_conn = None
        conn = _get_off_conn()
        if conn is None:
            return None
        row = conn.execute(
            "SELECT * FROM products WHERE code = ?", (barcode,)
        ).fetchone()

    if not row:
        return None

    row = dict(row)
    name = row.get("product_name") or ""
    ingredients = row.get("ingredients_text") or ""
    categories = row.get("categories") or ""

    return {
        "source": "Open Food Facts (local — 4.4M products)",
        "barcode": barcode,
        "gtin": _barcode_to_gtin(barcode),
        "product_name": name,
        "brand": row.get("brands") or "",
        "price": None,
        "ingredients_text": ingredients,
        "ingredients_list": [
            i.strip() for i in ingredients.split(",") if i.strip()
        ],
        "categories": categories,
        "hs_code": _infer_hs_code(name, categories),
        "image_url": row.get("image_url") or None,
        "quantity": row.get("quantity") or None,
        "countries": row.get("countries") or None,
        "nutriscore": row.get("nutriscore_grade") or None,
        "labels": row.get("labels") or None,
        "raw_data": {
            k: row.get(k)
            for k in ["nova_group", "origins", "stores"]
            if row.get(k)
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# PRICE LOOKUP
# ══════════════════════════════════════════════════════════════════════════════

def lookup_price_google(product_name: str, barcode: str = "") -> Optional[dict]:
    """
    Search for product pricing via web scraping.
    Tries multiple strategies:
      1. DuckDuckGo HTML search for "{product_name} price"
      2. barcodelookup.com (if barcode provided)
      3. Google Shopping lite search
    Returns {"price": float, "currency": str, "source": str} or None.
    """
    # Strategy 1: DuckDuckGo search
    price_info = _price_from_duckduckgo(product_name)
    if price_info:
        return price_info

    # Strategy 2: barcodelookup.com price extraction (if barcode available)
    if barcode:
        price_info = _price_from_barcodelookup(barcode)
        if price_info:
            return price_info

    # Strategy 3: DuckDuckGo with barcode
    if barcode:
        price_info = _price_from_duckduckgo(f"{barcode} price")
        if price_info:
            return price_info

    return None


def _price_from_duckduckgo(query: str) -> Optional[dict]:
    """Search DuckDuckGo HTML for price information."""
    try:
        search_url = "https://html.duckduckgo.com/html/"
        resp = requests.post(
            search_url,
            data={"q": f"{query} price USD"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return None

        html = resp.text

        # Look for price patterns in results: $X.XX, USD X.XX, X.XX USD
        price_patterns = [
            r'\$(\d{1,4}(?:\.\d{2})?)',              # $12.99
            r'USD\s*(\d{1,4}(?:\.\d{2})?)',           # USD 12.99
            r'(\d{1,4}\.\d{2})\s*(?:USD|dollars?)',   # 12.99 USD
            r'(?:price|cost)[:\s]*\$?(\d{1,4}\.\d{2})', # price: 12.99
        ]

        prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            for m in matches:
                try:
                    p = float(m)
                    if 0.10 <= p <= 9999.99:  # reasonable price range
                        prices.append(p)
                except ValueError:
                    continue

        if prices:
            # Return the median price to filter outliers
            prices.sort()
            median_price = prices[len(prices) // 2]
            return {
                "price": round(median_price, 2),
                "currency": "USD",
                "source": "web search",
            }

    except Exception as e:
        logger.debug(f"DuckDuckGo price search failed: {e}")

    return None


def _price_from_barcodelookup(barcode: str) -> Optional[dict]:
    """Extract price specifically from barcodelookup.com."""
    barcode = _normalise_barcode(barcode)
    url = f"https://www.barcodelookup.com/{barcode}"

    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }, timeout=8)
        if resp.status_code != 200:
            return None

        html = resp.text

        # barcodelookup.com shows prices in store listings
        price_patterns = [
            r'class="store-link-price"[^>]*>\s*\$?([\d,.]+)',
            r'class="product-price"[^>]*>\s*\$?([\d,.]+)',
            r'\$(\d{1,4}\.\d{2})',
        ]

        prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, html)
            for m in matches:
                try:
                    p = float(re.sub(r"[^\d.]", "", m))
                    if 0.10 <= p <= 9999.99:
                        prices.append(p)
                except ValueError:
                    continue

        if prices:
            return {
                "price": round(min(prices), 2),  # cheapest listed price
                "currency": "USD",
                "source": "barcodelookup.com",
            }

    except Exception as e:
        logger.debug(f"Barcode Lookup price extraction failed: {e}")

    return None


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED LOOKUP
# ══════════════════════════════════════════════════════════════════════════════

def lookup_barcode(barcode: str) -> dict:
    """
    Look up a barcode across all sources.
    Returns best result found, or an error dict.

    Lookup order:
      1. Local Open Food Facts data (if downloaded)
      2. Open Food Facts API
      3. Barcode Lookup (barcodelookup.com)
      4. UPC Food Search (upcfoodsearch.com)
    """
    barcode = _normalise_barcode(barcode)

    if not barcode or len(barcode) < 8:
        return {
            "status": "error",
            "message": f"Invalid barcode: '{barcode}'. Must be 8-14 digits (EAN-8/UPC-A/EAN-13/GTIN-14).",
            "barcode": barcode,
        }

    errors: list[str] = []

    # 1. Local OFF data
    result = lookup_openfoodfacts_local(barcode)
    if result:
        result["status"] = "found"
        return result

    # 2. Open Food Facts API
    result = lookup_openfoodfacts(barcode)
    if result:
        result["status"] = "found"
        return result
    errors.append("Open Food Facts: not found")

    # 3. Barcode Lookup
    result = lookup_barcodelookup(barcode)
    if result:
        result["status"] = "found"
        return result
    errors.append("Barcode Lookup: not found")

    # 4. UPC Food Search
    result = lookup_upcfoodsearch(barcode)
    if result:
        result["status"] = "found"
        return result
    errors.append("UPC Food Search: not found")

    return {
        "status": "not_found",
        "message": f"Barcode {barcode} not found in any source.",
        "barcode": barcode,
        "gtin": _barcode_to_gtin(barcode),
        "sources_checked": errors,
    }
