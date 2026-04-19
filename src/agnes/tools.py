"""
Agnes CPG Tool Execution.

Handles execution of all Agnes tool calls from the conversational agent.
Tool *definitions* live in prompt.py — this module contains the execute_tool
dispatcher and each tool's implementation logic.
"""

import json
from typing import Any

# Re-export tools for backwards compatibility
from .prompt import AGNES_TOOLS


def execute_tool(
    tool_name: str,
    tool_input: dict,
    cpg_db: Any,
    pipeline: Any,
) -> Any:
    """Execute an Agnes tool call and return the result."""

    # ── CPG Database ───────────────────────────────────────────────────────

    if tool_name == "search_ingredients":
        query = tool_input.get("query", "")
        limit = tool_input.get("limit", 10)
        results = cpg_db.search_ingredients(query)
        if not results:
            return {
                "found": False,
                "message": f"No ingredients found matching '{query}'",
                "results": [],
            }
        results = results[:limit]
        return {
            "found": True,
            "count": len(results),
            "results": [
                {
                    "ingredient_name": r.get("ingredient_name", r.get("name", "")),
                    "product_ids": r.get("product_ids", []),
                    "example_sku": r.get("example_sku", ""),
                }
                for r in results
            ],
        }

    elif tool_name == "get_ingredient_details":
        ingredient_name = tool_input.get("ingredient_name", "")
        idx = cpg_db._ingredient_index()
        demand_map = cpg_db.get_demand_map()

        match = None
        for item in idx:
            if item["ingredient_name"].lower() == ingredient_name.lower():
                match = item
                break
        if not match:
            for item in idx:
                if ingredient_name.lower() in item["ingredient_name"].lower():
                    match = item
                    break
        if not match:
            return {"error": f"Ingredient '{ingredient_name}' not found"}

        name = match["ingredient_name"]
        product_ids = match["product_ids"]

        suppliers = []
        for pid in product_ids:
            for s in cpg_db.get_suppliers_for_product(pid):
                if s["Name"] not in [sup["name"] for sup in suppliers]:
                    suppliers.append({"name": s["Name"], "product_id": pid})

        demand = demand_map.get(name, [])

        from src.procurement.substitution_engine import FUNCTIONAL_CATEGORIES
        func_cat = None
        for cat, members in FUNCTIONAL_CATEGORIES.items():
            if any(m.lower() in name.lower() or name.lower() in m.lower() for m in members):
                func_cat = cat
                break

        return {
            "ingredient_name": name,
            "product_ids": product_ids,
            "example_sku": match["example_sku"],
            "supplier_count": len(suppliers),
            "suppliers": suppliers,
            "bom_usage_count": len(demand),
            "companies_using": sorted({d["company"] for d in demand}),
            "functional_category": func_cat,
        }

    elif tool_name == "get_bom":
        fg_id = tool_input.get("finished_good_id")
        bom = cpg_db.get_bom(fg_id)
        if not bom:
            return {"error": f"No BOM found for finished good ID {fg_id}"}
        return {
            "finished_good_id": fg_id,
            "ingredient_count": len(bom),
            "ingredients": bom,
        }

    elif tool_name == "get_suppliers":
        ingredient_name = tool_input.get("ingredient_name", "")
        idx = cpg_db._ingredient_index()

        product_ids = []
        for item in idx:
            if item["ingredient_name"].lower() == ingredient_name.lower():
                product_ids = item["product_ids"]
                break
        if not product_ids:
            for item in idx:
                if ingredient_name.lower() in item["ingredient_name"].lower():
                    product_ids = item["product_ids"]
                    break
        if not product_ids:
            return {"error": f"Ingredient '{ingredient_name}' not found"}

        all_suppliers = []
        seen = set()
        for pid in product_ids:
            for s in cpg_db.get_suppliers_for_product(pid):
                if s["Name"] not in seen:
                    all_suppliers.append(s)
                    seen.add(s["Name"])

        return {
            "ingredient": ingredient_name,
            "supplier_count": len(all_suppliers),
            "suppliers": all_suppliers,
        }

    elif tool_name == "get_database_stats":
        return cpg_db.stats()

    # ── Agnes Pipeline ─────────────────────────────────────────────────────

    elif tool_name == "run_agnes_pipeline":
        ingredient_name = tool_input.get("ingredient_name", "")
        target_market = tool_input.get("target_market", "usa")
        product_form = tool_input.get("product_form")
        product_category = tool_input.get("product_category")

        try:
            result = pipeline.run(
                ingredient_name,
                target_market=target_market,
                product_form=product_form,
                product_category=product_category,
            )
            return result.to_dict()
        except Exception as e:
            return {"error": f"Pipeline error: {e}"}

    elif tool_name == "get_substitutes":
        ingredient_name = tool_input.get("ingredient_name", "")
        top_k = tool_input.get("top_k", 10)

        try:
            from src.procurement.substitution_engine import SubstitutionEngine
            engine = SubstitutionEngine(cpg_db)
            subs = engine.find_substitutes(ingredient_name, top_k=top_k)
            return {
                "ingredient": ingredient_name,
                "count": len(subs),
                "substitutes": subs,
            }
        except Exception as e:
            return {"error": f"Substitution error: {e}"}

    # ── Supplier Discovery & Scoring ───────────────────────────────────────

    elif tool_name == "discover_suppliers":
        ingredients = tool_input.get("ingredients", [])
        max_per_source = tool_input.get("max_per_source", 5)

        try:
            from src.procurement.supplier_discovery import discover_for_ingredients
            from src.procurement.supplier_db import SupplierDatabase

            results = discover_for_ingredients(ingredients, max_per_source=max_per_source)

            sdb = SupplierDatabase()
            sdb.clear_discovered()
            total_saved = 0
            for ingredient, suppliers in results.items():
                for s in suppliers:
                    s["scraped_by"] = "Agnes Auto-Discovery"
                    sdb.add_supplier(s)
                    total_saved += 1
            sdb.close()

            summary = {}
            for ingredient, suppliers in results.items():
                summary[ingredient] = {
                    "count": len(suppliers),
                    "countries": list({s.get("country", "Unknown") for s in suppliers}),
                    "sources": list({s.get("source_name", "Unknown") for s in suppliers}),
                }

            return {
                "total_discovered": sum(len(v) for v in results.values()),
                "total_saved_to_db": total_saved,
                "by_ingredient": summary,
            }
        except Exception as e:
            return {"error": f"Discovery error: {e}"}

    elif tool_name == "score_supplier":
        supplier_id = tool_input.get("supplier_id")
        try:
            from src.procurement.supplier_db import SupplierDatabase
            from src.procurement.supplier_scorer import score_supplier

            sdb = SupplierDatabase()
            supplier = sdb.get_supplier(supplier_id)
            sdb.close()

            if not supplier:
                return {"error": f"Supplier ID {supplier_id} not found"}

            scored = score_supplier(supplier)
            return {
                "supplier_name": scored.get("supplier_name"),
                "final_score": scored.get("final_score"),
                "tier_output": scored.get("tier_output"),
                "action": scored.get("action"),
                "score_breakdown": scored.get("score_breakdown"),
                "red_flags": scored.get("red_flags"),
            }
        except Exception as e:
            return {"error": f"Scoring error: {e}"}

    elif tool_name == "rank_all_suppliers":
        product_filter = tool_input.get("product_filter")
        try:
            from src.procurement.supplier_db import SupplierDatabase
            from src.procurement.supplier_scorer import rank_suppliers, tier_summary

            sdb = SupplierDatabase()
            if product_filter:
                suppliers = sdb.search_suppliers(product_filter)
            else:
                suppliers = sdb.get_all_suppliers()
            sdb.close()

            if not suppliers:
                return {"error": "No suppliers found in database"}

            ranked = rank_suppliers(suppliers)
            summary = tier_summary(ranked)

            return {
                "ranked_suppliers": [
                    {
                        "rank": s.get("rank"),
                        "supplier_name": s.get("supplier_name"),
                        "product": s.get("product"),
                        "country": s.get("country"),
                        "final_score": s.get("final_score"),
                        "tier_output": s.get("tier_output"),
                        "action": s.get("action"),
                    }
                    for s in ranked[:50]
                ],
                "summary": summary,
            }
        except Exception as e:
            return {"error": f"Ranking error: {e}"}

    # ── Compliance ─────────────────────────────────────────────────────────

    elif tool_name == "evaluate_compliance":
        supplier_name = tool_input.get("supplier_name", "")
        target_market = tool_input.get("target_market", "both")

        try:
            from src.procurement.compliance import evaluate_supplier
            report = evaluate_supplier(supplier_name, target_market=target_market)

            standards_out = []
            for s in report.standards:
                standards_out.append({
                    "standard": s.standard_name,
                    "category": s.category,
                    "score": s.score,
                    "max": s.max_points,
                    "evidence_level": s.evidence_level,
                    "details": s.details,
                    "red_flags": s.red_flags,
                })

            return {
                "supplier_name": report.supplier_name,
                "total_score": report.total_score,
                "max_score": report.max_score,
                "legal_score": report.legal_score,
                "quality_score": report.quality_score,
                "risk_level": report.risk_level,
                "standards": standards_out,
                "red_flags": report.red_flags,
            }
        except Exception as e:
            return {"error": f"Compliance error: {e}"}

    # ── HS Code ────────────────────────────────────────────────────────────

    elif tool_name == "lookup_hs_code":
        material = tool_input.get("material", "")
        try:
            from src.procurement.hs_lookup import get_hs_code
            return get_hs_code(material)
        except Exception as e:
            return {"error": f"HS code lookup error: {e}"}

    # ── Barcode ────────────────────────────────────────────────────────────

    elif tool_name == "lookup_barcode":
        barcode = tool_input.get("barcode", "")
        try:
            from src.procurement.barcode_lookup import lookup_barcode
            result = lookup_barcode(barcode)
            if result:
                return result
            return {"error": f"No product found for barcode '{barcode}'"}
        except Exception as e:
            return {"error": f"Barcode lookup error: {e}"}

    # ── Internal Procurement ───────────────────────────────────────────────

    elif tool_name == "check_internal":
        material_name = tool_input.get("material_name", "")
        try:
            from src.procurement.internal_checker import InternalChecker
            checker = InternalChecker(cpg_db=cpg_db)
            return checker.check(material_name)
        except Exception as e:
            return {"error": f"Internal check error: {e}"}

    # ── Product Identification ─────────────────────────────────────────────

    elif tool_name == "identify_product":
        query = tool_input.get("query", "")
        industry = tool_input.get("industry")
        use_case = tool_input.get("use_case")
        try:
            from src.procurement.product_identifier import ProductIdentifier
            identifier = ProductIdentifier(cpg_db=cpg_db)
            return identifier.identify(query, industry=industry, use_case=use_case)
        except Exception as e:
            return {"error": f"Product identification error: {e}"}

    # ── Supply Intelligence ────────────────────────────────────────────────

    elif tool_name == "gather_supply_intelligence":
        material_name = tool_input.get("material_name", "")
        hsn_code = tool_input.get("hsn_code")
        layers = tool_input.get("layers")
        try:
            from src.procurement.supply_intelligence import SupplyIntelligenceGatherer
            gatherer = SupplyIntelligenceGatherer(mode="offline")
            result = gatherer.gather(material_name, hsn_code=hsn_code, layers=layers)
            return {
                "material": result.get("material"),
                "total_suppliers": len(result.get("suppliers", [])),
                "stats": result.get("stats"),
                "suppliers": result.get("suppliers", [])[:30],
            }
        except Exception as e:
            return {"error": f"Supply intelligence error: {e}"}

    # ── Supplier Database ──────────────────────────────────────────────────

    elif tool_name == "search_supplier_database":
        query = tool_input.get("query", "")
        try:
            from src.procurement.supplier_db import SupplierDatabase
            sdb = SupplierDatabase()
            results = sdb.search_suppliers(query)
            sdb.close()
            return {
                "query": query,
                "count": len(results),
                "suppliers": results[:30],
            }
        except Exception as e:
            return {"error": f"Supplier DB search error: {e}"}

    elif tool_name == "get_supplier_stats":
        try:
            from src.procurement.supplier_db import SupplierDatabase
            sdb = SupplierDatabase()
            stats = sdb.get_stats()
            sdb.close()
            return stats
        except Exception as e:
            return {"error": f"Supplier stats error: {e}"}

    elif tool_name == "get_red_flag_suppliers":
        try:
            from src.procurement.supplier_db import SupplierDatabase
            sdb = SupplierDatabase()
            flagged = sdb.get_red_flag_suppliers()
            sdb.close()
            return {
                "count": len(flagged),
                "suppliers": flagged,
            }
        except Exception as e:
            return {"error": f"Red flag query error: {e}"}

    # ── Gmail ──────────────────────────────────────────────────────────────

    elif tool_name == "sync_gmail_inbox":
        try:
            from src.agnes.gmail_sync import GmailInboxStore
            store = GmailInboxStore()
            try:
                return store.sync_mailbox(
                    query=tool_input.get("query"),
                    label_ids=tool_input.get("label_ids"),
                    max_messages=tool_input.get("max_messages"),
                )
            finally:
                store.close()
        except Exception as e:
            return {"error": f"Gmail sync error: {e}"}

    elif tool_name == "search_stored_gmail":
        try:
            from src.agnes.gmail_sync import GmailInboxStore
            store = GmailInboxStore()
            try:
                return store.search_messages(
                    tool_input.get("query", ""),
                    limit=tool_input.get("limit", 20),
                )
            finally:
                store.close()
        except Exception as e:
            return {"error": f"Gmail search error: {e}"}

    # ── Consolidated Sourcing ──────────────────────────────────────────────

    elif tool_name == "analyze_consolidation":
        ingredient_name = tool_input.get("ingredient_name", "")
        compliance_region = tool_input.get("compliance_region", "usa")
        try:
            from src.procurement.consolidated_sourcing import ConsolidatedSourcingEngine
            from src.procurement.substitution_engine import SubstitutionEngine
            sub_engine = SubstitutionEngine(cpg_db)
            consolidation = ConsolidatedSourcingEngine(cpg_db, sub_engine)
            result = consolidation.recommend_consolidation(
                ingredient_name,
                compliance_region=compliance_region,
            )
            return result
        except Exception as e:
            return {"error": f"Consolidation error: {e}"}

    # ── Bottleneck Analysis ────────────────────────────────────────────────

    elif tool_name == "analyze_bottleneck":
        ingredient_name = tool_input.get("ingredient_name", "")
        try:
            from .actions import analyze_bottleneck
            return analyze_bottleneck(ingredient_name, cpg_db)
        except Exception as e:
            return {"error": f"Bottleneck analysis error: {e}"}

    # ── Full Ingredient Analysis ───────────────────────────────────────────

    elif tool_name == "analyze_ingredient":
        ingredient_name = tool_input.get("ingredient_name", "")
        try:
            from .actions import analyze_ingredient
            return analyze_ingredient(ingredient_name, cpg_db)
        except Exception as e:
            return {"error": f"Ingredient analysis error: {e}"}

    # ── Unknown Tool ───────────────────────────────────────────────────────

    else:
        return {"error": f"Unknown tool: {tool_name}"}
