"""
Step 1: Intake & Context Assembly for the Agnes CPG Pipeline.

Builds 8 context blocks from db.sqlite data, providing the structured
context that downstream Agnes steps (candidate generation, scoring,
recommendation) consume.

P1 fixes applied:
  - FIX 1: Block 5 uses 'dominant_co_ingredient_pattern' (purely structural),
    no functional inference — cluster interpretation belongs in Step 3.
  - FIX 2: Block 8 splits sensitivity flags into hard_constraints and
    soft_sensitivities, with clean_label_is_declared_claim toggle.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase

# Regulatory regime keywords by market
_REGIME_KEYWORDS: dict[str, dict] = {
    "usa": {
        "regime_name": "FDA / DSHEA",
        "keywords": [
            "GRAS", "NDI", "DSHEA", "cGMP", "21 CFR",
            "Supplement Facts", "Prop 65",
        ],
    },
    "eu": {
        "regime_name": "EFSA / EU Food Safety",
        "keywords": [
            "Novel Food", "EFSA", "EU 2015/2283", "EU 1169/2011",
            "health claims regulation", "EU 432/2012",
        ],
    },
}


class AgnesContext:
    """Step 1: Intake & Context Assembly for the Agnes CPG Pipeline."""

    def __init__(self, cpg_db: CpgDatabase):
        self.db = cpg_db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(
        self,
        ingredient_name: str,
        run_scope: str = "single_ingredient",
        product_form: str | None = None,
        product_category: str | None = None,
        target_market: str = "usa",
        compliance_strictness: str = "standard",
        sensitivity_flags: dict | None = None,
        finished_good_id: int | None = None,
    ) -> dict:
        """Build all 8 context blocks.

        Returns a dict with keys:
            target, product, demand, supply, formulation,
            variant, compliance, sensitivity
        """
        # Resolve canonical name
        from src.procurement.cpg_db import _canon
        canonical = _canon(ingredient_name) if ingredient_name else ingredient_name

        # Pre-fetch shared data used by multiple blocks
        demand_map = self.db.get_demand_map()
        ingredient_demand = demand_map.get(canonical, [])
        bom_sets = self.db.bom_ingredient_sets()
        ing_to_boms = self.db.ingredient_to_boms()
        ingredient_index = self.db._ingredient_index()

        return {
            "target": self._block_target(
                canonical, run_scope, finished_good_id, ingredient_demand,
            ),
            "product": self._block_product(
                product_form, product_category, finished_good_id,
            ),
            "demand": self._block_demand(
                canonical, ingredient_demand, bom_sets,
            ),
            "supply": self._block_supply(
                canonical, ingredient_index, ingredient_demand,
            ),
            "formulation": self._block_formulation(
                canonical, ing_to_boms, bom_sets,
            ),
            "variant": self._block_variant(
                canonical, ingredient_index, ingredient_demand,
            ),
            "compliance": self._block_compliance(
                target_market, compliance_strictness,
            ),
            "sensitivity": self._block_sensitivity(
                sensitivity_flags,
            ),
        }

    # ------------------------------------------------------------------
    # Block 1 — Target Context
    # ------------------------------------------------------------------

    def _block_target(
        self,
        canonical: str,
        run_scope: str,
        finished_good_id: int | None,
        ingredient_demand: list[dict],
    ) -> dict:
        """Run scope, canonical name, affected finished goods."""
        affected_fgs = sorted(
            {d["finished_good"] for d in ingredient_demand}
        )

        block: dict = {
            "run_scope": run_scope,
            "canonical_ingredient_name": canonical,
            "affected_finished_goods": affected_fgs,
            "affected_finished_good_count": len(affected_fgs),
        }

        if finished_good_id is not None:
            block["finished_good_id"] = finished_good_id
            bom = self.db.get_bom(finished_good_id)
            block["finished_good_bom_size"] = len(bom)

        return block

    # ------------------------------------------------------------------
    # Block 2 — Product Context
    # ------------------------------------------------------------------

    def _block_product(
        self,
        product_form: str | None,
        product_category: str | None,
        finished_good_id: int | None,
    ) -> dict:
        """Product form, category, intended use."""
        block: dict = {
            "product_form": product_form,        # tablet, capsule, powder, etc.
            "product_category": product_category,  # supplement, food, cosmetic, OTC
            "intended_use": _infer_intended_use(product_category),
        }

        if finished_good_id is not None:
            fg_rows = self.db._q(
                "SELECT SKU FROM Product WHERE Id = ?", (finished_good_id,)
            )
            if fg_rows:
                block["finished_good_sku"] = fg_rows[0]["SKU"]

        return block

    # ------------------------------------------------------------------
    # Block 3 — Demand Context
    # ------------------------------------------------------------------

    def _block_demand(
        self,
        canonical: str,
        ingredient_demand: list[dict],
        bom_sets: dict[int, set[str]],
    ) -> dict:
        """BOM frequency, company frequency, portfolio centrality, usage concentration."""
        bom_ids = {d["bom_id"] for d in ingredient_demand}
        companies = {d["company"] for d in ingredient_demand}

        total_boms = len(bom_sets)
        bom_count = len(bom_ids)
        centrality = round(bom_count / total_boms, 4) if total_boms else 0.0

        return {
            "bom_frequency": bom_count,
            "company_frequency": len(companies),
            "companies": sorted(companies),
            "portfolio_centrality_score": centrality,
            "usage_concentration": "broad" if len(companies) >= 3 else "narrow",
            "total_boms_in_db": total_boms,
        }

    # ------------------------------------------------------------------
    # Block 4 — Supply Context
    # ------------------------------------------------------------------

    def _block_supply(
        self,
        canonical: str,
        ingredient_index: list[dict],
        ingredient_demand: list[dict],
    ) -> dict:
        """Supplier count, single-source flags, supplier overlap, dominant share."""
        # Collect all product IDs for this canonical ingredient
        product_ids: list[int] = []
        for item in ingredient_index:
            if item["ingredient_name"].lower() == canonical.lower():
                product_ids = item["product_ids"]
                break

        # Gather suppliers across all product ID variants
        supplier_counter: Counter = Counter()
        product_suppliers: dict[int, list[str]] = {}
        for pid in product_ids:
            suppliers = self.db.get_suppliers_for_product(pid)
            names = [s["Name"] for s in suppliers]
            product_suppliers[pid] = names
            for name in names:
                supplier_counter[name] += 1

        all_suppliers = sorted(supplier_counter.keys())
        supplier_count = len(all_suppliers)

        # Single-source flags: per company, check if only 1 supplier covers
        # that company's product IDs for this ingredient
        companies_demand = defaultdict(set)
        for d in ingredient_demand:
            companies_demand[d["company"]].add(d["bom_id"])

        # Dominant supplier share
        total_links = sum(supplier_counter.values())
        dominant_supplier = None
        dominant_share = 0.0
        if supplier_counter:
            dominant_supplier, dom_count = supplier_counter.most_common(1)[0]
            dominant_share = round(dom_count / total_links, 4) if total_links else 0.0

        # Single-source detection: any product ID with exactly 1 supplier
        single_source_product_ids = [
            pid for pid, sups in product_suppliers.items()
            if len(sups) == 1
        ]

        return {
            "supplier_count": supplier_count,
            "suppliers": all_suppliers,
            "single_source_product_ids": single_source_product_ids,
            "single_source_risk": len(single_source_product_ids) > 0,
            "dominant_supplier": dominant_supplier,
            "dominant_supplier_share": dominant_share,
            "supplier_product_link_count": total_links,
        }

    # ------------------------------------------------------------------
    # Block 5 — Formulation Context  (FIX 1 applied)
    # ------------------------------------------------------------------

    def _block_formulation(
        self,
        canonical: str,
        ing_to_boms: dict[str, set[int]],
        bom_sets: dict[int, set[str]],
    ) -> dict:
        """Top co-ingredients, co-occurrence strength, dominant_co_ingredient_pattern.

        FIX 1 (P1): The field is 'dominant_co_ingredient_pattern' — a purely
        structural/descriptive summary of which ingredients appear together.
        NO functional inference is made here; that belongs in Step 3.
        """
        target_boms = ing_to_boms.get(canonical, set())
        if not target_boms:
            return {
                "co_ingredient_count": 0,
                "top_co_ingredients": [],
                "co_occurrence_scores": {},
                "dominant_co_ingredient_pattern": [],
                "bom_sample_size": 0,
            }

        # Count how often each other ingredient co-occurs with the target
        co_counter: Counter = Counter()
        for bom_id in target_boms:
            for ing in bom_sets.get(bom_id, set()):
                if ing.lower() != canonical.lower():
                    co_counter[ing] += 1

        bom_sample_size = len(target_boms)
        top5 = co_counter.most_common(5)

        # Co-occurrence strength: fraction of target's BOMs that also contain
        # the co-ingredient
        co_scores = {
            name: round(count / bom_sample_size, 4)
            for name, count in top5
        }

        # dominant_co_ingredient_pattern: the set of ingredients that appear
        # together in > 50 % of the target ingredient's BOMs (purely structural)
        pattern_threshold = 0.5
        pattern = sorted(
            name for name, count in co_counter.items()
            if count / bom_sample_size >= pattern_threshold
        )

        return {
            "co_ingredient_count": len(co_counter),
            "top_co_ingredients": [name for name, _ in top5],
            "co_occurrence_scores": co_scores,
            "dominant_co_ingredient_pattern": pattern,
            "bom_sample_size": bom_sample_size,
        }

    # ------------------------------------------------------------------
    # Block 6 — Variant Context
    # ------------------------------------------------------------------

    def _block_variant(
        self,
        canonical: str,
        ingredient_index: list[dict],
        ingredient_demand: list[dict],
    ) -> dict:
        """SKU variants collapsed via _canon(), aliases, demand counts."""
        from src.procurement.cpg_db import _canon

        # Find all raw-material SKUs that map to the same canonical name
        matching_items: list[dict] = []
        alias_skus: list[str] = []
        for item in ingredient_index:
            if item["ingredient_name"].lower() == canonical.lower():
                matching_items.append(item)
                alias_skus.append(item["example_sku"])

        # Also look for near-matches (canonical names that contain or are
        # contained by the target) to surface aliases
        related: list[str] = []
        for item in ingredient_index:
            name = item["ingredient_name"]
            if name.lower() == canonical.lower():
                continue
            if canonical.lower() in name.lower() or name.lower() in canonical.lower():
                related.append(name)

        total_product_ids = sum(
            len(item["product_ids"]) for item in matching_items
        )

        return {
            "canonical_name": canonical,
            "sku_aliases": alias_skus,
            "related_names": sorted(related),
            "variant_product_id_count": total_product_ids,
            "demand_record_count": len(ingredient_demand),
        }

    # ------------------------------------------------------------------
    # Block 7 — Compliance Context
    # ------------------------------------------------------------------

    def _block_compliance(
        self,
        target_market: str,
        compliance_strictness: str,
    ) -> dict:
        """Target market, regulatory regime names, strictness, keywords."""
        markets = (
            ["usa", "eu"] if target_market == "both" else [target_market]
        )

        regimes: list[dict] = []
        all_keywords: list[str] = []
        for mkt in markets:
            info = _REGIME_KEYWORDS.get(mkt)
            if info:
                regimes.append({
                    "market": mkt,
                    "regime_name": info["regime_name"],
                    "keywords": info["keywords"],
                })
                all_keywords.extend(info["keywords"])

        return {
            "target_market": target_market,
            "compliance_strictness": compliance_strictness,
            "regulatory_regimes": regimes,
            "all_regime_keywords": sorted(set(all_keywords)),
        }

    # ------------------------------------------------------------------
    # Block 8 — Sensitivity Flags  (FIX 2 applied)
    # ------------------------------------------------------------------

    def _block_sensitivity(
        self,
        sensitivity_flags: dict | None,
    ) -> dict:
        """Split into hard_constraints and soft_sensitivities.

        FIX 2 (P1):
          hard_constraints  — allergen_sensitive, organic_certification_required,
                              vegan_vegetarian: regulatory/labeling consequences.
          soft_sensitivities — premium_positioned, child_focused,
                               clean_label_preference: commercial scoring modifiers,
                               NOT gates.
          Special case: clean_label is a hard constraint ONLY when it is a
          declared claim on the finished product label.
        """
        flags = sensitivity_flags or {}

        clean_label_is_declared = flags.get("clean_label_is_declared_claim", False)

        hard_constraints: dict = {
            "allergen_sensitive": flags.get("allergen_sensitive", False),
            "organic_certification_required": flags.get(
                "organic_certification_required", False
            ),
            "vegan_vegetarian": flags.get("vegan_vegetarian", False),
        }

        soft_sensitivities: dict = {
            "premium_positioned": flags.get("premium_positioned", False),
            "child_focused": flags.get("child_focused", False),
            "clean_label_preference": flags.get("clean_label_preference", False),
        }

        # If clean_label is a declared claim on the finished product label,
        # promote it from soft sensitivity to hard constraint.
        if clean_label_is_declared:
            hard_constraints["clean_label"] = True
            soft_sensitivities.pop("clean_label_preference", None)

        return {
            "clean_label_is_declared_claim": clean_label_is_declared,
            "hard_constraints": hard_constraints,
            "soft_sensitivities": soft_sensitivities,
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _infer_intended_use(product_category: str | None) -> str | None:
    """Map product category to a plain-language intended-use string."""
    mapping = {
        "supplement": "dietary supplementation",
        "food": "food / beverage ingredient",
        "cosmetic": "cosmetic / personal care",
        "otc": "over-the-counter therapeutic",
    }
    if product_category:
        return mapping.get(product_category.lower())
    return None
