"""
Product Identifier — Stage 1 of the Procurement Pipeline.

Responsibilities:
  - Autocomplete: match prefix against CPG ingredient database
  - Identify: fuzzy-match user query to a CPG ingredient record
  - Resolve HSN/HS tariff code for trade-data unlock
"""

from __future__ import annotations

from typing import Optional

# ── HSN code map ─────────────────────────────────────────────────────────────
# Maps material name keywords / family names to HS chapter codes.
# Used when no exact universe match is found.

HSN_CODES: dict[str, str] = {
    # CPG / supplement ingredients
    "whey protein": "0404",
    "casein": "3501",
    "gelatin": "3503",
    "lecithin": "2923",
    "soy lecithin": "2923",
    "sunflower lecithin": "2923",
    "vitamin": "2936",
    "ascorbic acid": "2936",
    "tocopherol": "2936",
    "magnesium stearate": "2915",
    "stearic acid": "2915",
    "citric acid": "2918",
    "cellulose": "3912",
    "microcrystalline cellulose": "3912",
    "xanthan gum": "1301",
    "guar gum": "1301",
    "carrageenan": "1302",
    "sucralose": "2932",
    "stevia": "1302",
    "fish oil": "1504",
    "omega 3": "1504",
    "collagen": "3503",
    "biotin": "2936",
    "folic acid": "2936",
    "calcium carbonate": "2836",
    "magnesium oxide": "2519",
    "zinc oxide": "2817",
    "iron": "2821",
    "ferrous fumarate": "2917",
    "titanium dioxide": "2823",
    "silicon dioxide": "2811",
    "maltodextrin": "1702",
    "dextrose": "1702",
    "fructose": "1702",
    "flavoring": "3302",
    "natural flavor": "3302",
    "artificial flavor": "3302",
}

class ProductIdentifier:
    """
    Identifies a CPG ingredient from a free-text query and resolves it
    to a structured record including material_id and HSN tariff code.
    """

    def __init__(self, cpg_db=None, **_kwargs):
        self._cpg_db = cpg_db

    # ── public: autocomplete ─────────────────────────────────────────────────

    def autocomplete(self, prefix: str, limit: int = 12) -> list[dict]:
        """Return up to `limit` CPG ingredients matching `prefix`."""
        if not prefix or not self._cpg_db:
            return []

        results = []
        seen: set[str] = set()
        cpg_results = self._cpg_db.search_ingredients(prefix, limit=limit)
        for cr in cpg_results:
            cid = f"CPG-{cr['ingredient_name']}"
            if cid not in seen:
                results.append({
                    "material_id": cid,
                    "name": cr["ingredient_name"],
                    "category": "cpg_ingredient",
                    "family": "supplement",
                    "description": f"CPG raw material ({len(cr.get('product_ids', []))} variants in DB)",
                    "matched_text": cr["ingredient_name"],
                    "source": "cpg_sqlite",
                })
                seen.add(cid)

        return results[:limit]

    # ── public: identify ─────────────────────────────────────────────────────

    def identify(
        self,
        query: str,
        industry: str = None,
        use_case: str = None,
    ) -> dict:
        """
        Match `query` to a CPG ingredient and resolve its HSN code.
        """
        if self._cpg_db:
            cpg_hits = self._cpg_db.search_ingredients(query, limit=1)
            if cpg_hits:
                hit = cpg_hits[0]
                cpg_name = hit["ingredient_name"]
                from difflib import SequenceMatcher as SM
                cpg_sim = SM(None, query.lower().strip(), cpg_name.lower()).ratio()

                suppliers = []
                for pid in hit.get("product_ids", [])[:3]:
                    suppliers.extend(self._cpg_db.get_suppliers_for_product(pid))
                supplier_names = sorted(set(s["Name"] for s in suppliers))
                return {
                    "status": "identified",
                    "confidence": round(max(cpg_sim, 0.85), 2),
                    "material_id": f"CPG-{cpg_name}",
                    "name": cpg_name,
                    "category": "cpg_ingredient",
                    "family": "supplement",
                    "subcategory": "raw material",
                    "description": f"CPG ingredient ({len(hit.get('product_ids', []))} variants in database)",
                    "standards": [],
                    "applications": [],
                    "forms_available": [],
                    "cas": "",
                    "hsn_code": self._hsn_from_text(cpg_name),
                    "industry_context": industry,
                    "use_case_context": use_case,
                    "query": query,
                    "source": "cpg_sqlite",
                    "cpg_product_ids": hit.get("product_ids", []),
                    "cpg_sku": hit.get("example_sku", ""),
                    "cpg_suppliers": supplier_names,
                }

        # Not found — return a skeleton so the pipeline can still run
        return {
            "status": "unresolved",
            "confidence": 0.0,
            "material_id": None,
            "name": query,
            "category": "unknown",
            "family": "unknown",
            "subcategory": "",
            "description": "",
            "standards": [],
            "applications": [],
            "forms_available": [],
            "cas": "",
            "hsn_code": self._hsn_from_text(query),
            "industry_context": industry,
            "use_case_context": use_case,
            "query": query,
            "source": "user_query",
        }

    # ── HSN resolution ────────────────────────────────────────────────────────

    def _hsn_from_text(self, text: str) -> str:
        t = text.lower()
        for keyword, code in HSN_CODES.items():
            if keyword in t:
                return code
        return "2106"  # misc food preparations — CPG fallback
