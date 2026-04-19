"""
Agnes CPG Tool Definitions.
These are the tools Agnes (Claude) uses to interact with CPG procurement data
and the 7-step substitution pipeline.
"""

import json
from typing import Any

# Tool definitions for the Claude API tool_use format
AGNES_TOOLS = [
    {
        "name": "search_ingredients",
        "description": (
            "Search the CPG ingredient database (876 raw materials) for ingredients "
            "matching a query. Returns matching ingredients with their canonical names, "
            "product IDs, supplier counts, and BOM usage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (ingredient name, partial name, or category)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_agnes_pipeline",
        "description": (
            "Run the full 7-step Agnes substitution intelligence pipeline for an "
            "ingredient. Returns scored candidates, consolidation scenarios, "
            "recommendation frames, and a human review package with gap analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "The ingredient to analyze (e.g. 'soy lecithin')",
                },
                "target_market": {
                    "type": "string",
                    "enum": ["usa", "eu", "both"],
                    "description": "Compliance region (default: usa)",
                    "default": "usa",
                },
                "product_form": {
                    "type": "string",
                    "enum": ["tablet", "capsule", "powder", "gummy", "liquid", "softgel"],
                    "description": "Product form constraint (optional)",
                },
                "product_category": {
                    "type": "string",
                    "enum": ["supplement", "food", "cosmetic", "otc"],
                    "description": "Product category (optional)",
                },
                "max_candidates": {
                    "type": "integer",
                    "description": "Maximum candidates to generate (default: 20)",
                    "default": 20,
                },
            },
            "required": ["ingredient_name"],
        },
    },
    {
        "name": "get_substitutes",
        "description": (
            "Find substitute ingredients for a given ingredient using the substitution "
            "engine. Returns ranked substitutes with 3-signal scores (name similarity, "
            "BOM co-occurrence, functional category)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "Ingredient to find substitutes for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of substitutes to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["ingredient_name"],
        },
    },
    {
        "name": "get_ingredient_details",
        "description": (
            "Get full details for an ingredient: suppliers, BOM usage, variant SKUs, "
            "demand map, and functional category."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "The ingredient name to look up",
                },
            },
            "required": ["ingredient_name"],
        },
    },
    {
        "name": "get_bom",
        "description": (
            "Get the Bill of Materials (ingredient list) for a finished product. "
            "Returns all raw material ingredients with their product IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "finished_good_id": {
                    "type": "integer",
                    "description": "The finished good product ID",
                },
            },
            "required": ["finished_good_id"],
        },
    },
    {
        "name": "get_suppliers",
        "description": (
            "Get all suppliers for an ingredient, including their names, "
            "product links, and the full supplier catalog."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "Ingredient name to find suppliers for",
                },
            },
            "required": ["ingredient_name"],
        },
    },
    {
        "name": "get_database_stats",
        "description": (
            "Get overview statistics of the CPG database: company count, "
            "ingredient count, BOM count, supplier count, and product counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lookup_barcode",
        "description": (
            "Look up a product by barcode (EAN/UPC). Returns product name, brand, "
            "ingredients list, categories, and HS code from a database of 4.4M products."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "barcode": {
                    "type": "string",
                    "description": "The barcode number (EAN-13, UPC-A, etc.)",
                },
            },
            "required": ["barcode"],
        },
    },
    {
        "name": "sync_gmail_inbox",
        "description": (
            "Read Gmail messages through the Gmail API and store them in Agnes's "
            "local SQLite mail store for later retrieval and analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search query such as 'label:inbox newer_than:30d'",
                },
                "label_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional Gmail label IDs to filter the sync",
                },
                "max_messages": {
                    "type": "integer",
                    "description": "Maximum number of messages to fetch in this run",
                },
            },
        },
    },
    {
        "name": "search_stored_gmail",
        "description": (
            "Search previously synced Gmail messages from Agnes's local mail store "
            "by sender, subject, snippet, recipients, or body text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search in locally stored messages",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum stored messages to return",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
]


def execute_tool(
    tool_name: str,
    tool_input: dict,
    cpg_db: Any,
    pipeline: Any,
) -> Any:
    """
    Execute an Agnes tool call and return the result.
    """

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

    elif tool_name == "run_agnes_pipeline":
        ingredient_name = tool_input.get("ingredient_name", "")
        target_market = tool_input.get("target_market", "usa")
        product_form = tool_input.get("product_form")
        product_category = tool_input.get("product_category")
        max_candidates = tool_input.get("max_candidates", 20)

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

    elif tool_name == "get_ingredient_details":
        ingredient_name = tool_input.get("ingredient_name", "")
        idx = cpg_db._ingredient_index()
        demand_map = cpg_db.get_demand_map()

        # Find matching ingredient
        match = None
        for item in idx:
            if item["ingredient_name"].lower() == ingredient_name.lower():
                match = item
                break

        if not match:
            # Try partial match
            for item in idx:
                if ingredient_name.lower() in item["ingredient_name"].lower():
                    match = item
                    break

        if not match:
            return {"error": f"Ingredient '{ingredient_name}' not found"}

        name = match["ingredient_name"]
        product_ids = match["product_ids"]

        # Get suppliers
        suppliers = []
        for pid in product_ids:
            for s in cpg_db.get_suppliers_for_product(pid):
                if s["Name"] not in [sup["name"] for sup in suppliers]:
                    suppliers.append({"name": s["Name"], "product_id": pid})

        # Get demand
        demand = demand_map.get(name, [])

        # Get functional category
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

    else:
        return {"error": f"Unknown tool: {tool_name}"}
