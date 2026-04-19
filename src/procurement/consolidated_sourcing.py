"""
Consolidated Sourcing Engine.

Aggregates demand across companies buying the same or substitutable
ingredients and recommends supplier consolidation strategies.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .evidence import EvidenceTrail

if TYPE_CHECKING:
    from .cpg_db import CpgDatabase
    from .substitution_engine import SubstitutionEngine


class ConsolidatedSourcingEngine:
    """Produce supplier consolidation recommendations."""

    def __init__(
        self,
        cpg_db: CpgDatabase,
        substitution_engine: SubstitutionEngine,
    ):
        self.db = cpg_db
        self.sub_engine = substitution_engine

    # ── Demand matrix ──────────────────────────────────────────────────

    def build_demand_matrix(self) -> dict[str, dict]:
        """
        Cluster ingredients into substitution groups and aggregate demand.

        Returns: {group_key: {
            canonical_name, variants, total_bom_count, companies,
            current_suppliers, single_sourced
        }}
        """
        demand_map = self.db.get_demand_map()
        supplier_catalog = self.db.get_supplier_catalog()

        # Invert supplier catalog for quick lookup
        ingredient_suppliers: dict[str, set[str]] = defaultdict(set)
        for supplier, ingredients in supplier_catalog.items():
            for ing in ingredients:
                ingredient_suppliers[ing].add(supplier)

        # Build groups: start from categories, then add uncategorised
        categories = self.sub_engine.get_functional_categories()
        grouped: dict[str, dict] = {}

        for cat, members in categories.items():
            companies = set()
            total_bom = 0
            all_suppliers = set()
            for m in members:
                for usage in demand_map.get(m, []):
                    companies.add(usage["company"])
                    total_bom += 1
                all_suppliers |= ingredient_suppliers.get(m, set())

            if total_bom == 0:
                continue

            grouped[cat] = {
                "canonical_name": cat.replace("_", " ").title(),
                "variants": members,
                "total_bom_count": total_bom,
                "companies": sorted(companies),
                "current_suppliers": sorted(all_suppliers),
                "single_sourced": len(all_suppliers) <= 1,
            }

        return grouped

    # ── Consolidation recommendation ───────────────────────────────────

    def recommend_consolidation(
        self,
        ingredient_or_group: str,
        compliance_region: str = "usa",
        evidence: EvidenceTrail | None = None,
    ) -> dict:
        """Generate a consolidation recommendation for an ingredient/group."""

        query = ingredient_or_group.lower().strip()

        # Find substitutes
        substitutes = self.sub_engine.find_substitutes(
            query, max_results=15, min_similarity=0.15, evidence=evidence
        )

        # Gather all variants (query + substitutes)
        variant_names = [query] + [s["ingredient"] for s in substitutes]

        # Aggregate demand
        demand_map = self.db.get_demand_map()
        supplier_catalog = self.db.get_supplier_catalog()
        ingredient_suppliers: dict[str, set[str]] = defaultdict(set)
        for supplier, ingredients in supplier_catalog.items():
            for ing in ingredients:
                ingredient_suppliers[ing].add(supplier)

        companies_affected = set()
        boms_affected = set()
        for v in variant_names:
            for usage in demand_map.get(v, []):
                companies_affected.add(usage["company"])
                boms_affected.add(usage["bom_id"])

        # Score each supplier by how many variants they can supply
        supplier_scores: dict[str, dict] = {}
        for v in variant_names:
            for sup in ingredient_suppliers.get(v, set()):
                if sup not in supplier_scores:
                    supplier_scores[sup] = {
                        "name": sup,
                        "products_offered": [],
                        "variant_count": 0,
                    }
                supplier_scores[sup]["products_offered"].append(v)
                supplier_scores[sup]["variant_count"] += 1

        # Consolidation score: how much of the group a single supplier covers
        total_variants = max(len(variant_names), 1)
        preferred = []
        for sup_data in supplier_scores.values():
            coverage = sup_data["variant_count"] / total_variants
            # Also factor in how many companies they'd serve
            sup_companies = set()
            for v in sup_data["products_offered"]:
                for usage in demand_map.get(v, []):
                    sup_companies.add(usage["company"])
            sup_data["companies_served"] = len(sup_companies)
            sup_data["consolidation_score"] = round(
                100 * (0.6 * coverage + 0.4 * (len(sup_companies) / max(len(companies_affected), 1))),
                1,
            )
            preferred.append(sup_data)

        preferred.sort(key=lambda s: s["consolidation_score"], reverse=True)

        # Build substitution opportunities
        sub_opportunities = []
        for s in substitutes[:5]:
            opp = {
                "from": query,
                "to": s["ingredient"],
                "reason": [],
                "savings_potential": "unknown",
            }
            if s["evidence"]["category_match"]:
                opp["reason"].append(f"Same functional category ({s['evidence']['functional_category']})")
            if s["evidence"]["bom_cooccurrence"] > 0.1:
                opp["reason"].append("Used in similar formulations")
            if len(s["suppliers"]) > len(ingredient_suppliers.get(query, set())):
                opp["reason"].append("More suppliers available (better pricing leverage)")
                opp["savings_potential"] = "medium-high"
            opp["reason"] = "; ".join(opp["reason"]) if opp["reason"] else "Composite similarity match"
            sub_opportunities.append(opp)

        # Narrative recommendation
        top_sup = preferred[0]["name"] if preferred else "N/A"
        rec = (
            f"Consolidate {len(variant_names)} variant(s) of '{query}' group across "
            f"{len(companies_affected)} companies and {len(boms_affected)} BOMs. "
            f"Top supplier candidate: {top_sup} "
            f"(covers {preferred[0]['variant_count']}/{total_variants} variants, "
            f"serves {preferred[0]['companies_served']} companies)."
            if preferred
            else f"No supplier data found for '{query}'."
        )

        result = {
            "ingredient_group": query,
            "recommendation": rec,
            "preferred_suppliers": preferred[:10],
            "demand_summary": {
                "companies_affected": len(companies_affected),
                "boms_affected": len(boms_affected),
                "total_variants": len(variant_names),
                "substitution_opportunities": sub_opportunities,
            },
            "evidence_trail": [],
        }

        if evidence:
            evidence.add(
                stage="consolidation",
                claim=rec,
                source="ConsolidatedSourcingEngine",
                confidence=0.85 if preferred else 0.3,
                data_ref={
                    "companies_affected": len(companies_affected),
                    "boms_affected": len(boms_affected),
                    "top_supplier": top_sup,
                },
            )
            result["evidence_trail"] = evidence.to_dict()

        return result

    # ── Full report ────────────────────────────────────────────────────

    def full_report(self, top_n: int = 20, compliance_region: str = "usa") -> list[dict]:
        """Top N ingredient groups by demand with consolidation recs."""
        matrix = self.build_demand_matrix()
        # Sort by total_bom_count descending
        sorted_groups = sorted(
            matrix.items(), key=lambda kv: kv[1]["total_bom_count"], reverse=True
        )
        report = []
        for key, group in sorted_groups[:top_n]:
            rec = self.recommend_consolidation(
                group["variants"][0] if group["variants"] else key,
                compliance_region=compliance_region,
            )
            rec["group_info"] = group
            report.append(rec)
        return report
