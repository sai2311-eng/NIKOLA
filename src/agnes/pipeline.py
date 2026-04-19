"""
Agnes 7-Step CPG Pipeline Orchestrator.

Connects all 7 steps into a single run:
    1. Intake & Context Assembly   (context.py)
    2. Candidate Generation        (candidates.py)
    3. Constraint Inference         (constraints.py)
    4. Evidence Collection          (evidence_collector.py)
    5. Feasibility Scoring          (scoring.py)
    6. Consolidation & Recommendation (consolidation.py)
    7. Human Review & Approval      (review.py)

Key design principles (from spec):
    - Generate before filtering: high recall at Step 2, precision at Steps 3-5
    - Evidence is a first-class object with source, authority, verification, staleness
    - Confidence and verification are separate dimensions
    - Conflicts surface explicitly, never averaged or hidden
    - All 4 scoring dimensions: 0-100, higher is better
    - Composite score ranks; it never overrides a hard gate
    - Every decision is logged with evidence basis, gaps, and rationale
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Optional, TYPE_CHECKING

from .context import AgnesContext
from .candidates import CandidateGenerator, Candidate
from .constraints import ConstraintInference
from .evidence_collector import EvidenceCollector
from .scoring import FeasibilityScorer
from .consolidation import ConsolidationModeler
from .review import ReviewBuilder

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase


@dataclass
class AgnesRunResult:
    """Complete output of a single Agnes pipeline run."""

    ingredient_name: str
    run_id: str
    status: str  # "completed" | "partial" | "error"
    timing_ms: dict  # step_name -> elapsed_ms
    context: dict  # Step 1 output
    candidates: list  # Step 2 output (Candidate objects)
    constraints: object  # Step 3 output (ConstraintSet)
    evidence_map: dict  # Step 4 output {candidate_name: [EvidenceRecord]}
    scorecards: list  # Step 5 output (ScoreCard objects)
    scenarios: list  # Step 6 output (Scenario objects)
    recommendations: list  # Step 6 output (Recommendation objects)
    review_package: object  # Step 7 output (ReviewPackage)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize the full result for JSON output."""
        return {
            "ingredient_name": self.ingredient_name,
            "run_id": self.run_id,
            "status": self.status,
            "timing_ms": self.timing_ms,
            "context": self.context,
            "candidate_count": len(self.candidates),
            "candidates": [
                {
                    "canonical_name": c.canonical_name,
                    "candidate_type": c.candidate_type,
                    "signals": c.signals,
                    "validation_status": c.validation_status,
                    "source_signals": c.source_signals,
                }
                if isinstance(c, Candidate) else c
                for c in self.candidates
            ],
            "constraints": (
                asdict(self.constraints) if hasattr(self.constraints, "__dataclass_fields__")
                else self.constraints
            ),
            "evidence_summary": {
                name: len(records)
                for name, records in self.evidence_map.items()
            },
            "scorecards": [
                sc.to_dict() if hasattr(sc, "to_dict") else sc
                for sc in self.scorecards
            ],
            "scenarios": [
                s.to_dict() if hasattr(s, "to_dict") else s
                for s in self.scenarios
            ],
            "recommendations": [
                r.to_dict() if hasattr(r, "to_dict") else r
                for r in self.recommendations
            ],
            "review_package": (
                self.review_package.to_dict()
                if hasattr(self.review_package, "to_dict")
                else self.review_package
            ),
            "errors": self.errors,
        }


class AgnesPipeline:
    """
    Orchestrates the Agnes 7-step CPG substitution intelligence pipeline.

    Usage::

        from src.procurement.cpg_db import CpgDatabase
        from src.agnes.pipeline import AgnesPipeline

        db = CpgDatabase("db.sqlite")
        pipeline = AgnesPipeline(db)
        result = pipeline.run("soy lecithin")
    """

    def __init__(
        self,
        cpg_db: CpgDatabase,
        scoring_weights: Optional[dict] = None,
        max_candidates: int = 30,
    ):
        self.db = cpg_db
        self.scoring_weights = scoring_weights
        self.max_candidates = max_candidates

        # Lazy-init step modules
        self._context: Optional[AgnesContext] = None
        self._generator: Optional[CandidateGenerator] = None
        self._constraints: Optional[ConstraintInference] = None
        self._evidence: Optional[EvidenceCollector] = None
        self._scorer: Optional[FeasibilityScorer] = None
        self._consolidation: Optional[ConsolidationModeler] = None
        self._reviewer: Optional[ReviewBuilder] = None

    # ── Lazy accessors ────────────────────────────────────────────────

    @property
    def context_builder(self) -> AgnesContext:
        if self._context is None:
            self._context = AgnesContext(self.db)
        return self._context

    @property
    def candidate_generator(self) -> CandidateGenerator:
        if self._generator is None:
            self._generator = CandidateGenerator(self.db)
        return self._generator

    @property
    def constraint_inference(self) -> ConstraintInference:
        if self._constraints is None:
            self._constraints = ConstraintInference(self.db)
        return self._constraints

    @property
    def evidence_collector(self) -> EvidenceCollector:
        if self._evidence is None:
            self._evidence = EvidenceCollector(self.db)
        return self._evidence

    @property
    def feasibility_scorer(self) -> FeasibilityScorer:
        if self._scorer is None:
            self._scorer = FeasibilityScorer(self.db, weights=self.scoring_weights)
        return self._scorer

    @property
    def consolidation_modeler(self) -> ConsolidationModeler:
        if self._consolidation is None:
            self._consolidation = ConsolidationModeler(self.db)
        return self._consolidation

    @property
    def review_builder(self) -> ReviewBuilder:
        if self._reviewer is None:
            self._reviewer = ReviewBuilder()
        return self._reviewer

    # ── Main entry point ──────────────────────────────────────────────

    def run(
        self,
        ingredient_name: str,
        *,
        run_scope: str = "single_ingredient",
        product_form: Optional[str] = None,
        product_category: Optional[str] = None,
        target_market: str = "usa",
        compliance_strictness: str = "standard",
        sensitivity_flags: Optional[dict] = None,
        finished_good_id: Optional[int] = None,
    ) -> AgnesRunResult:
        """
        Execute the full 7-step Agnes pipeline for an ingredient.

        Parameters
        ----------
        ingredient_name : str
            The ingredient to analyze (e.g. "soy lecithin").
        run_scope : str
            "single_ingredient", "finished_good", "ingredient_group", or
            "consolidation_scenario".
        product_form : str, optional
            "tablet", "capsule", "powder", "gummy", "liquid", "softgel".
        product_category : str, optional
            "supplement", "food", "cosmetic", "otc".
        target_market : str
            "usa", "eu", or "both".
        compliance_strictness : str
            "standard" or "strict".
        sensitivity_flags : dict, optional
            Hard and soft sensitivity flags (see context.py Block 8).
        finished_good_id : int, optional
            Specific finished-good ID for single-product runs.

        Returns
        -------
        AgnesRunResult
            Complete pipeline output with all 7 steps.
        """
        import uuid
        run_id = uuid.uuid4().hex[:12]
        timing: dict[str, float] = {}
        errors: list[str] = []

        # ── Step 1: Intake & Context Assembly ─────────────────────────
        t0 = time.perf_counter()
        try:
            context = self.context_builder.build(
                ingredient_name=ingredient_name,
                run_scope=run_scope,
                product_form=product_form,
                product_category=product_category,
                target_market=target_market,
                compliance_strictness=compliance_strictness,
                sensitivity_flags=sensitivity_flags,
                finished_good_id=finished_good_id,
            )
        except Exception as e:
            errors.append(f"Step 1 (Context): {e}")
            context = {}
        timing["step1_context"] = _elapsed_ms(t0)

        # ── Step 2: Candidate Generation ──────────────────────────────
        t0 = time.perf_counter()
        try:
            # Build the input context dict that CandidateGenerator expects
            gen_context = {
                "ingredient_name": ingredient_name,
                "product_form": product_form,
                "sensitivity_flags": _extract_hard_flag_names(context),
                "excluded_ingredients": [],
            }
            candidates = self.candidate_generator.generate(
                gen_context, max_candidates=self.max_candidates
            )
        except Exception as e:
            errors.append(f"Step 2 (Candidates): {e}")
            candidates = []
        timing["step2_candidates"] = _elapsed_ms(t0)

        # ── Step 3: Constraint Inference ──────────────────────────────
        t0 = time.perf_counter()
        try:
            constraint_set = self.constraint_inference.infer(context, candidates)
        except Exception as e:
            errors.append(f"Step 3 (Constraints): {e}")
            constraint_set = None
        timing["step3_constraints"] = _elapsed_ms(t0)

        # ── Step 4: Evidence Collection ───────────────────────────────
        t0 = time.perf_counter()
        try:
            # Convert Candidate dataclass objects to dicts for evidence collector
            cand_dicts = [
                {
                    "ingredient_name": c.canonical_name,
                    "name": c.canonical_name,
                    "product_ids": c.product_ids,
                    "candidate_type": c.candidate_type,
                    "signals": c.signals,
                }
                if isinstance(c, Candidate) else c
                for c in candidates
            ]
            raw_evidence_map = self.evidence_collector.collect_all(cand_dicts, context)
            # Convert EvidenceRecord dataclasses to dicts for downstream consumers
            evidence_map = _evidence_map_to_dicts(raw_evidence_map)
        except Exception as e:
            errors.append(f"Step 4 (Evidence): {e}")
            evidence_map = {}
        timing["step4_evidence"] = _elapsed_ms(t0)

        # ── Step 5: Feasibility Scoring ───────────────────────────────
        t0 = time.perf_counter()
        try:
            scorecards = self.feasibility_scorer.score_all(
                cand_dicts, context, evidence_map
            )
        except Exception as e:
            errors.append(f"Step 5 (Scoring): {e}")
            scorecards = []
        timing["step5_scoring"] = _elapsed_ms(t0)

        # ── Step 6: Consolidation & Recommendation ────────────────────
        t0 = time.perf_counter()
        scenarios = []
        recommendations = []
        try:
            # Build consolidation context from pipeline data
            consol_context = _build_consolidation_context(context, evidence_map)
            # Convert scorecards to the dict format consolidation expects
            scored_candidates = _scorecards_to_candidate_dicts(scorecards)
            scenarios = self.consolidation_modeler.model_scenarios(
                consol_context, scored_candidates
            )
            recommendations = self.consolidation_modeler.recommend(scenarios)
        except Exception as e:
            errors.append(f"Step 6 (Consolidation): {e}")
        timing["step6_consolidation"] = _elapsed_ms(t0)

        # ── Step 7: Human Review & Approval ───────────────────────────
        t0 = time.perf_counter()
        review_package = None
        try:
            review_package = self.review_builder.build(
                ingredient_name=ingredient_name,
                context=context,
                scorecards=scorecards,
                scenarios=scenarios,
                recommendations=recommendations,
                evidence_map=evidence_map,
            )
        except Exception as e:
            errors.append(f"Step 7 (Review): {e}")
        timing["step7_review"] = _elapsed_ms(t0)

        status = "completed" if not errors else ("partial" if candidates else "error")

        return AgnesRunResult(
            ingredient_name=ingredient_name,
            run_id=run_id,
            status=status,
            timing_ms=timing,
            context=context,
            candidates=candidates,
            constraints=constraint_set,
            evidence_map=evidence_map,
            scorecards=scorecards,
            scenarios=scenarios,
            recommendations=recommendations,
            review_package=review_package,
            errors=errors,
        )


# ── Helpers ───────────────────────────────────────────────────────────


def _evidence_map_to_dicts(raw_map: dict) -> dict:
    """Convert EvidenceRecord objects to plain dicts for scoring/review consumers."""
    from dataclasses import asdict
    result = {}
    for name, records in raw_map.items():
        result[name] = [
            asdict(r) if hasattr(r, "__dataclass_fields__") else r
            for r in records
        ]
    return result


def _elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _extract_hard_flag_names(context: dict) -> list[str]:
    """Extract active hard constraint flag names from context for candidate tagging."""
    sensitivity = context.get("sensitivity", {})
    hard = sensitivity.get("hard_constraints", {})
    return [k for k, v in hard.items() if v]


def _build_consolidation_context(context: dict, evidence_map: dict) -> dict:
    """Build the context dict that ConsolidationModeler expects."""
    demand = context.get("demand", {})
    supply = context.get("supply", {})
    companies = demand.get("companies", [])

    # Evidence quality: average trust weight across all evidence records
    all_records = [r for records in evidence_map.values() for r in records]
    if all_records:
        ev_quality = sum(
            getattr(r, "trust_weight", 0.5) for r in all_records
        ) / len(all_records)
    else:
        ev_quality = 0.5

    # Count gaps (candidates with < 3 evidence records)
    gap_count = sum(
        1 for records in evidence_map.values() if len(records) < 3
    )

    return {
        "companies": companies,
        "current_suppliers": supply.get("suppliers", []),
        "centrality_scores": {c: 1.0 for c in companies},
        "company_compliance": {},
        "current_assignments": {},
        "evidence_quality": ev_quality,
        "gap_count": gap_count,
    }


def _scorecards_to_candidate_dicts(scorecards: list) -> list[dict]:
    """Convert ScoreCard objects to the dict format consolidation expects."""
    result = []
    for sc in scorecards:
        if hasattr(sc, "gate_status") and sc.gate_status != "passed":
            continue  # skip blocked candidates
        d = {
            "name": sc.candidate_name if hasattr(sc, "candidate_name") else str(sc),
            "composite_score": sc.composite if hasattr(sc, "composite") else 0.0,
        }
        if hasattr(sc, "scores"):
            d["scores"] = sc.scores
        result.append(d)
    return result
