"""
Procurement Intelligence Pipeline — 8-Stage CPG Orchestrator
=============================================================

Stage 1  Product Identification
         fuzzy-matches query → CPG ingredient + HSN/HS code

Stage 2  Materials Universe Lookup (legacy — CPG uses db.sqlite)

Stage 3  Internal Check
         queries internal stock / BOM / CPG suppliers
         in_stock  → still runs substitution/consolidation
         no_stock  → full external sourcing

Stage 4  Supply Intelligence Gathering  (Alibaba + IndiaMART)
Stage 5  Ranking Engine  (Quality · Compliance · Price · Lead Time)
Stage 6  Assessment Dashboard
Stage 7  Substitution Analysis  (3-signal engine)
Stage 8  Consolidated Sourcing  (supplier aggregation)

Usage
-----
    from procurement_pipeline import ProcurementPipeline

    pipeline = ProcurementPipeline(mode="offline")
    result   = pipeline.run("whey protein isolate")

CLI
---
    python procurement_pipeline.py "soy lecithin" --compliance usa
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.procurement.product_identifier import ProductIdentifier
from src.procurement.internal_checker import InternalChecker
from src.procurement.supply_intelligence import SupplyIntelligenceGatherer
from src.procurement.ranking import ProcurementRanker
from src.procurement.cpg_db import CpgDatabase
from src.procurement.substitution_engine import SubstitutionEngine
from src.procurement.consolidated_sourcing import ConsolidatedSourcingEngine
from src.procurement.evidence import EvidenceTrail

_DEFAULT_DB = _ROOT / "db.sqlite"


class ProcurementPipeline:
    """
    CPG procurement intelligence pipeline.

    Parameters
    ----------
    mode              : "offline" (Selenium/scraping) or "api" (LLM-assisted)
    anthropic_api_key : required for api mode
    intel_layers      : which intelligence layers to run (default [9, 10])
    internal_dir      : path to internal_procurement/ folder
    db_path           : path to CPG SQLite database
    compliance_region : "eu" or "usa"
    """

    STAGE_NAMES = {
        1: "Product Identification",
        2: "CPG Database Lookup",
        3: "Internal Check",
        4: "Supply Intelligence Gathering",
        5: "Ranking Engine",
        6: "Assessment Dashboard",
        7: "Substitution Analysis",
        8: "Consolidated Sourcing",
    }

    def __init__(
        self,
        mode: str = "offline",
        anthropic_api_key: str = None,
        intel_layers: list[int] = None,
        internal_dir: Path = None,
        db_path: Path = None,
        compliance_region: str = "usa",
    ):
        self.mode = mode
        self.api_key = anthropic_api_key
        self.intel_layers = intel_layers or [9, 10]
        self.compliance_region = compliance_region

        # CPG database
        db = db_path or _DEFAULT_DB
        if Path(db).exists():
            self.cpg_db = CpgDatabase(db)
            self.sub_engine = SubstitutionEngine(self.cpg_db)
            self.consol_engine = ConsolidatedSourcingEngine(
                self.cpg_db, self.sub_engine
            )
        else:
            self.cpg_db = None
            self.sub_engine = None
            self.consol_engine = None

        self.identifier    = ProductIdentifier(cpg_db=self.cpg_db)
        self.int_checker   = InternalChecker(internal_dir, cpg_db=self.cpg_db)
        self.gatherer      = SupplyIntelligenceGatherer(
            mode=mode, anthropic_api_key=anthropic_api_key
        )
        self.ranker        = ProcurementRanker()

    # ── public helpers ────────────────────────────────────────────────────────

    def autocomplete(self, prefix: str, limit: int = 12) -> list[dict]:
        """Return materials matching a prefix — feeds the live search-bar."""
        return self.identifier.autocomplete(prefix, limit=limit)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _dashboard(
        self,
        product: dict,
        universe_rec: Optional[dict],
        internal: dict,
        intel: Optional[dict],
        ranked: list[dict],
    ) -> dict:
        geo: dict[str, int] = {}
        by_region: dict[str, list] = {}
        for s in ranked:
            r = s.get("region", "unknown")
            geo[r] = geo.get(r, 0) + 1
            by_region.setdefault(r, []).append({
                "name":    s.get("supplier_name"),
                "score":   s.get("composite_score"),
                "verdict": s.get("verdict"),
                "website": s.get("website"),
            })

        layer_coverage: dict[str, int] = {}
        if intel:
            for ln, suppliers in intel.get("by_layer", {}).items():
                ln_int = int(ln) if isinstance(ln, str) else ln
                lname = SupplyIntelligenceGatherer.LAYERS.get(ln_int, f"Layer {ln}")
                layer_coverage[lname] = len(suppliers)

        verdicts = {"excellent": 0, "good": 0, "possible": 0,
                    "limited": 0, "poor": 0}
        for s in ranked:
            v = s.get("verdict", "poor")
            verdicts[v] = verdicts.get(v, 0) + 1

        return {
            "generated": str(date.today()),
            "material": {
                "name":           product.get("name"),
                "material_id":    product.get("material_id"),
                "category":       product.get("category"),
                "family":         product.get("family"),
                "hsn_code":       product.get("hsn_code"),
                "description":    product.get("description"),
                "standards":      product.get("standards", []),
                "forms_available":product.get("forms_available", []),
                "industry":       product.get("industry_context"),
                "use_case":       product.get("use_case_context"),
            },
            "universe_properties": (
                universe_rec.get("properties") if universe_rec else None
            ),
            "internal_status": {
                "status":         internal.get("status"),
                "last_supplier":  internal.get("last_supplier"),
                "last_price_usd": internal.get("last_price_usd"),
                "avg_price_usd":  internal.get("avg_price_usd"),
                "total_ordered":  internal.get("total_ordered"),
                "record_count":   internal.get("record_count", 0),
                "quality_hold":   internal.get("quality_hold", False),
            },
            "supplier_summary": {
                "total_found": len(ranked),
                **verdicts,
            },
            "geographic_distribution": geo,
            "suppliers_by_region":     by_region,
            "layer_coverage":          layer_coverage,
            "top_10": [
                {
                    "rank":            s["rank"],
                    "supplier_name":   s.get("supplier_name"),
                    "region":          s.get("region"),
                    "composite_score": s.get("composite_score"),
                    "verdict":         s.get("verdict"),
                    "scores":          s.get("scores", {}),
                    "price_usd":       s.get("price_usd"),
                    "lead_time_days":  s.get("lead_time_days"),
                    "moq":             s.get("moq"),
                    "certifications":  s.get("certifications", []),
                    "website":         s.get("website"),
                    "layer":           s.get("layer"),
                    "layer_name":      s.get("layer_name"),
                }
                for s in ranked[:10]
            ],
            "approved_internal_suppliers": internal.get("approved_suppliers", []),
        }

    # ── main pipeline ─────────────────────────────────────────────────────────

    def run(
        self,
        query: str,
        industry: str = None,
        use_case: str = None,
        ranking_weights: dict = None,
        active_signals: list[str] = None,
        intel_layers: list[int] = None,
        progress_callback: Optional[Callable] = None,
        compliance_region: str = None,
    ) -> dict:
        """
        Execute all pipeline stages and return structured result.

        Parameters
        ----------
        query             : material name / free-text description
        industry          : industry context e.g. "automotive"
        use_case          : application context e.g. "exhaust manifold"
        ranking_weights   : override default signal weights
        active_signals    : use only these signals in ranking
        progress_callback : fn(stage_num, stage_name, status, data=None)

        Returns
        -------
        {
          "query"            : original query,
          "stages"           : per-stage timing & result summaries,
          "product"          : identified CPG ingredient record,
          "internal_check"   : availability check result,
          "ranked_suppliers" : scored & sorted supplier list,
          "dashboard"        : Assessment Dashboard,
          "substitutes"      : substitution analysis results,
          "consolidation"    : consolidated sourcing recommendations,
          "recommendation"   : "available_internally" | "external_sourcing_required",
          "pipeline_complete": bool,
        }
        """
        region = compliance_region or self.compliance_region
        evidence = EvidenceTrail()

        result: dict = {
            "query":             query,
            "industry":          industry,
            "use_case":          use_case,
            "mode":              self.mode,
            "cpg_mode":          True,
            "compliance_region": region,
            "stages":            {},
            "pipeline_complete": False,
        }

        def _cb(stage, name, status, data=None):
            if progress_callback:
                progress_callback(stage, name, status, data)

        # ── Stage 1: Product Identification ──────────────────────────────────
        _cb(1, self.STAGE_NAMES[1], "running")
        t0 = time.time()
        product = self.identifier.identify(query, industry, use_case)
        result["stages"]["1_product_identification"] = {
            "duration_s": round(time.time() - t0, 2),
            "status":     product.get("status"),
            "confidence": product.get("confidence"),
        }
        result["product"] = product
        evidence.add(
            stage="identification",
            claim=f"Identified '{product.get('name')}' (confidence {product.get('confidence', 0):.0%})",
            source=product.get("source", "materials_universe"),
            confidence=product.get("confidence", 0),
        )
        _cb(1, self.STAGE_NAMES[1], "done", product)

        # ── Stage 2: CPG Database Lookup ──────────────────────────────────────
        _cb(2, self.STAGE_NAMES[2], "running")
        t0 = time.time()
        universe_rec = None  # CPG mode — no materials universe
        result["stages"]["2_cpg_lookup"] = {
            "duration_s": round(time.time() - t0, 2),
            "found":      product.get("source") == "cpg_sqlite",
        }
        result["universe_record"] = universe_rec
        _cb(2, self.STAGE_NAMES[2],
            "found in CPG DB" if product.get("source") == "cpg_sqlite" else "not in CPG DB")

        # ── Stage 3: Internal Check ───────────────────────────────────────────
        _cb(3, self.STAGE_NAMES[3], "running")
        t0 = time.time()
        internal = self.int_checker.check(
            product.get("name", query),
            product.get("material_id"),
        )
        result["stages"]["3_internal_check"] = {
            "duration_s":   round(time.time() - t0, 2),
            "status":       internal.get("status"),
            "record_count": internal.get("record_count", 0),
        }
        result["internal_check"] = internal
        evidence.add(
            stage="internal_check",
            claim=internal.get("message", ""),
            source="internal_procurement records + CPG database",
            confidence=0.95 if internal.get("status") == "in_stock" else 0.5,
        )
        _cb(3, self.STAGE_NAMES[3], internal.get("status", "checked"), internal)

        if internal.get("status") == "in_stock":
            # Ingredient exists in CPG DB — still run substitution/consolidation
            result["recommendation"] = "available_internally"
            _cb(3, self.STAGE_NAMES[3], "available in CPG database -- running substitution analysis")

        # ── Stage 4: Supply Intelligence Gathering ────────────────────────────
        _cb(4, self.STAGE_NAMES[4], "running")
        t0 = time.time()

        def _intel_cb(ln, lname, status):
            _cb(4, f"  Layer {ln}: {lname}", status)

        intel = self.gatherer.gather(
            material_name    = product.get("name", query),
            hsn_code         = product.get("hsn_code"),
            layers           = intel_layers or self.intel_layers,
            max_workers      = 4,
            progress_callback= _intel_cb,
        )
        result["stages"]["4_supply_intelligence"] = {
            "duration_s": round(time.time() - t0, 2),
            "stats":      intel.get("stats", {}),
        }
        result["intelligence"] = intel
        _cb(4, self.STAGE_NAMES[4], "done", intel.get("stats"))

        # ── Stage 5: Ranking Engine ───────────────────────────────────────────
        _cb(5, self.STAGE_NAMES[5], "running")
        t0 = time.time()

        ref_price = (
            internal.get("avg_price_usd")
            or internal.get("last_price_usd")
        )

        ranked = self.ranker.rank(
            suppliers               = intel.get("suppliers", []),
            material                = product,
            weights                 = ranking_weights,
            reference_price         = ref_price,
            active_signals          = active_signals,
            compliance_region       = region,
        )
        result["stages"]["5_ranking"] = {
            "duration_s":   round(time.time() - t0, 2),
            "total_ranked": len(ranked),
            "top_score":    ranked[0]["composite_score"] if ranked else None,
        }
        result["ranked_suppliers"] = ranked
        _cb(5, self.STAGE_NAMES[5], f"done ({len(ranked)} suppliers ranked)")

        # ── Stage 6: Material Assessment Dashboard ────────────────────────────
        _cb(6, self.STAGE_NAMES[6], "building")
        result["dashboard"] = self._dashboard(
            product, universe_rec, internal, intel, ranked
        )
        _cb(6, self.STAGE_NAMES[6], "done")

        if not result.get("recommendation"):
            result["recommendation"] = "external_sourcing_required"

        # ── Stage 7 & 8: Substitution & Consolidated Sourcing ─────────────
        if self.sub_engine:
            _cb(7, self.STAGE_NAMES[7], "running")
            t0 = time.time()
            ingredient_name = product.get("name", query)
            substitutes = self.sub_engine.find_substitutes(
                ingredient_name, max_results=10, evidence=evidence
            )
            result["stages"]["7_substitution"] = {
                "duration_s": round(time.time() - t0, 2),
                "substitutes_found": len(substitutes),
            }
            result["substitutes"] = substitutes
            _cb(7, self.STAGE_NAMES[7], f"done ({len(substitutes)} found)")

            _cb(8, self.STAGE_NAMES[8], "running")
            t0 = time.time()
            consolidation = self.consol_engine.recommend_consolidation(
                ingredient_name, compliance_region=region, evidence=evidence
            )
            result["stages"]["8_consolidation"] = {
                "duration_s": round(time.time() - t0, 2),
            }
            result["consolidation"] = consolidation
            _cb(8, self.STAGE_NAMES[8], "done")

        result["evidence_trail"] = evidence.to_dict()
        result["pipeline_complete"] = True
        return result


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli_progress(stage, name, status, data=None):
    print(f"  [Stage {stage}] {name}  ->  {status}")


def main():
    parser = argparse.ArgumentParser(
        description="SAI — CPG Procurement Intelligence Pipeline"
    )
    parser.add_argument("query", help="CPG ingredient name or description")
    parser.add_argument("--industry",  default=None)
    parser.add_argument("--use-case",  default=None, dest="use_case")
    parser.add_argument("--mode",      default="offline",
                        choices=["offline", "api"])
    parser.add_argument("--api-key",   default=None, dest="api_key")
    parser.add_argument("--layers",    default=None,
                        help="Comma-separated layer numbers e.g. 9,10")
    parser.add_argument("--output",    default=None,
                        help="Save JSON result to this file")
    parser.add_argument("--top",       default=20, type=int,
                        help="Show top N ranked suppliers")
    parser.add_argument("--compliance", default="usa", choices=["eu", "usa"],
                        help="Compliance region: eu or usa (default: usa)")
    parser.add_argument("--db",        default=None,
                        help="Path to CPG SQLite database")
    args = parser.parse_args()

    layers = (
        [int(x) for x in args.layers.split(",") if x.strip()]
        if args.layers else None
    )

    print("\n" + "=" * 60)
    print("  SAI — CPG Procurement Intelligence Pipeline")
    print("=" * 60)
    print(f"  Query      : {args.query}")
    print(f"  Compliance : {args.compliance.upper()}")
    print(f"  Industry   : {args.industry or '-'}")
    print(f"  Use case   : {args.use_case or '-'}")
    print(f"  Mode       : {args.mode}")
    print("=" * 60 + "\n")

    pipeline = ProcurementPipeline(
        mode              = args.mode,
        anthropic_api_key = args.api_key,
        intel_layers      = layers,
        db_path           = args.db,
        compliance_region = args.compliance,
    )

    result = pipeline.run(
        query             = args.query,
        industry          = args.industry,
        use_case          = args.use_case,
        progress_callback = _cli_progress,
        compliance_region = args.compliance,
    )

    # ── print summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RESULT SUMMARY")
    print("=" * 60)

    prod = result.get("product", {})
    print(f"\n  Material     : {prod.get('name')}  "
          f"(confidence {prod.get('confidence', 0):.0%})")
    print(f"  Category     : {prod.get('category')}  /  {prod.get('family')}")
    print(f"  HSN Code     : {prod.get('hsn_code')}")
    print(f"  Universe     : {'found' if result.get('universe_record') else 'not in DB'}")

    ic = result.get("internal_check", {})
    print(f"\n  Internal Check : {ic.get('status').upper()}")
    if ic.get("last_supplier"):
        print(f"    Last supplier  : {ic['last_supplier']}")
    if ic.get("avg_price_usd"):
        print(f"    Avg price      : ${ic['avg_price_usd']:.4f}/unit")

    print(f"\n  Recommendation : {result.get('recommendation', '—').upper()}")

    ranked = result.get("ranked_suppliers", [])
    if ranked:
        print(f"\n  Top {min(args.top, len(ranked))} Suppliers:")
        print(f"  {'#':>3}  {'Supplier':<35}  {'Region':<18}  {'Score':>5}  Verdict")
        print("  " + "-" * 75)
        for s in ranked[: args.top]:
            print(
                f"  {s['rank']:>3}  "
                f"{(s.get('supplier_name') or '—')[:35]:<35}  "
                f"{(s.get('region') or '—')[:18]:<18}  "
                f"{s.get('composite_score', 0):>5.1f}  "
                f"{s.get('verdict', '—')}"
            )

    # ── optional JSON export ──────────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"\n  JSON saved to: {out_path}")

    print("\n" + "=" * 60 + "\n")
    return result


if __name__ == "__main__":
    main()
