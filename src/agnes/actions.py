"""
Agnes Actions — high-level action functions for the conversational UI.

Three entry points that wrap existing pipeline modules:
  1. analyze_ingredient  — find suppliers + substitutes + demand aggregation
  2. analyze_barcode     — scan product → extract ingredients → suppliers per ingredient
  3. analyze_bottleneck  — emphasize supply risk + substitutes for disruption scenarios
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase


def analyze_ingredient(ingredient_name: str, db: CpgDatabase) -> dict:
    """Find suppliers, rank them (USA compliance default), find substitutes,
    and show cross-company demand aggregation for an ingredient.

    Returns
    -------
    dict with keys: ingredient, matches, suppliers, substitutes,
                    demand_aggregation, hs_code
    """
    from src.procurement.ranking import ProcurementRanker
    from src.procurement.substitution_engine import SubstitutionEngine
    from src.procurement.barcode_lookup import _infer_hs_code

    # 1. Search for the ingredient in the database
    matches = db.search_ingredients(ingredient_name, limit=10)
    if not matches:
        # Still try substitutes via web fallback
        engine = SubstitutionEngine(db)
        substitutes = engine.find_substitutes(ingredient_name, max_results=10)
        return {
            "ingredient": ingredient_name,
            "matches": [],
            "suppliers": [],
            "substitutes": substitutes,
            "demand_aggregation": {"companies": [], "total_usages": 0},
            "hs_code": _infer_hs_code(ingredient_name),
        }

    # Use the best match
    best = matches[0]

    # 2. Get all suppliers for this ingredient's product IDs and rank them
    ranker = ProcurementRanker()
    raw_suppliers: list[dict] = []
    seen_supplier_ids: set[int] = set()
    for pid in best.get("product_ids", []):
        for s in db.get_suppliers_for_product(pid):
            if s["Id"] not in seen_supplier_ids:
                seen_supplier_ids.add(s["Id"])
                raw_suppliers.append(s)

    ranked_suppliers = ranker.rank(
        raw_suppliers,
        compliance_region="usa",
    )

    # 3. Find substitutes (variant-filtered, with web fallback)
    engine = SubstitutionEngine(db)
    substitutes = engine.find_substitutes(ingredient_name, max_results=10)

    # 4. Demand aggregation — which companies use this ingredient
    demand_map = db.get_demand_map()
    demand_entries = demand_map.get(best["ingredient_name"], [])
    companies = sorted(set(d["company"] for d in demand_entries))

    return {
        "ingredient": best["ingredient_name"],
        "matches": matches,
        "suppliers": ranked_suppliers,
        "substitutes": substitutes,
        "demand_aggregation": {
            "companies": companies,
            "total_usages": len(demand_entries),
            "finished_goods": sorted(set(d["finished_good"] for d in demand_entries)),
        },
        "hs_code": _infer_hs_code(best["ingredient_name"]),
    }


def analyze_barcode(barcode: str, db: CpgDatabase) -> dict:
    """Look up a barcode, extract ingredients, and for each ingredient
    find suppliers + substitutes.

    Returns
    -------
    dict with keys: product, ingredients (each with suppliers + substitutes)
    """
    from src.procurement.barcode_lookup import lookup_barcode

    product = lookup_barcode(barcode)
    if product.get("status") != "found":
        return {"product": product, "ingredients": []}

    # Extract ingredient names from the product
    ingredient_names = product.get("ingredients_list", [])
    if not ingredient_names and product.get("ingredients_text"):
        ingredient_names = [
            i.strip() for i in product["ingredients_text"].split(",") if i.strip()
        ]

    # For each ingredient, do a lightweight analysis
    ingredients_analysis: list[dict] = []
    for ing_name in ingredient_names[:20]:  # cap at 20 ingredients
        analysis = analyze_ingredient(ing_name, db)
        ingredients_analysis.append({
            "name": ing_name,
            "found_in_db": len(analysis["matches"]) > 0,
            "db_name": analysis["ingredient"] if analysis["matches"] else None,
            "supplier_count": len(analysis["suppliers"]),
            "top_suppliers": analysis["suppliers"][:3],
            "substitute_count": len(analysis["substitutes"]),
            "top_substitutes": analysis["substitutes"][:3],
            "used_by_companies": analysis["demand_aggregation"]["companies"],
        })

    return {
        "product": product,
        "ingredients": ingredients_analysis,
    }


def analyze_bottleneck(ingredient_name: str, db: CpgDatabase) -> dict:
    """Analyze supply risk and find substitutes for a bottleneck scenario.

    Emphasizes:
    - Single-source risk detection
    - More substitutes (higher max_results)
    - Cross-company impact assessment

    Returns
    -------
    dict with keys: ingredient, risk_assessment, suppliers, substitutes,
                    impact, recommendations
    """
    from src.procurement.ranking import ProcurementRanker
    from src.procurement.substitution_engine import SubstitutionEngine

    # Base ingredient analysis
    base = analyze_ingredient(ingredient_name, db)

    # Risk assessment
    supplier_count = len(base["suppliers"])
    company_count = len(base["demand_aggregation"].get("companies", []))
    fg_count = len(base["demand_aggregation"].get("finished_goods", []))

    risk_level = "low"
    risk_factors: list[str] = []

    if supplier_count == 0:
        risk_level = "critical"
        risk_factors.append("No known suppliers in database")
    elif supplier_count == 1:
        risk_level = "high"
        risk_factors.append("Single-source dependency — no backup supplier")
    elif supplier_count == 2:
        risk_level = "medium"
        risk_factors.append("Limited supplier pool (only 2 suppliers)")

    if company_count > 3:
        risk_factors.append(f"High cross-company impact — {company_count} companies depend on this ingredient")
        if risk_level == "low":
            risk_level = "medium"

    if fg_count > 5:
        risk_factors.append(f"Used in {fg_count} finished goods — broad product impact")

    # Get more substitutes for bottleneck scenarios
    engine = SubstitutionEngine(db)
    substitutes = engine.find_substitutes(ingredient_name, max_results=20)

    # Build recommendations
    recommendations: list[str] = []
    if risk_level in ("critical", "high"):
        recommendations.append("Urgently identify and qualify alternative suppliers")
        if substitutes:
            top = substitutes[0]["ingredient"]
            recommendations.append(f"Evaluate '{top}' as primary substitute candidate")
    if supplier_count <= 2:
        recommendations.append("Diversify supplier base to reduce concentration risk")
    if substitutes:
        recommendations.append(f"{len(substitutes)} potential substitutes identified — begin qualification testing")
    if company_count > 1:
        recommendations.append(f"Coordinate with {company_count} affected companies for joint sourcing")

    return {
        "ingredient": base["ingredient"],
        "risk_assessment": {
            "risk_level": risk_level,
            "risk_factors": risk_factors,
            "supplier_count": supplier_count,
            "affected_companies": company_count,
            "affected_products": fg_count,
        },
        "suppliers": base["suppliers"],
        "substitutes": substitutes,
        "impact": base["demand_aggregation"],
        "recommendations": recommendations,
    }
