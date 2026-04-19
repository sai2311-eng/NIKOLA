"""
Agnes CPG Pipeline — Step 7: Human Review & Approval.

Packages all pipeline results into a structured review package for human
decision-makers.  Computes a structured confidence score, identifies data
gaps by severity, and suggests the appropriate decision mode.

Decision Modes:
    1. auto_approve       — all gates passed, confidence >= 0.85, no critical gaps
    2. review_recommended — confidence 0.65-0.85, minor/major gaps
    3. expert_required    — confidence < 0.65, or critical gaps present
    4. blocked            — hard gate failures, cannot proceed
    5. insufficient_data  — too many gaps, evidence quality < 0.3
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from .scoring import ScoreCard
from .consolidation import Scenario, Recommendation


# ---------------------------------------------------------------------------
# Evidence helper — lightweight stand-in when evidence_collector is not yet
# imported.  Callers pass evidence_map as dict[str, list[EvidenceRecord]].
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRecord:
    """Minimal evidence record expected by the review builder.

    Upstream modules may supply their own compatible type; only ``tier``
    and ``quality`` are inspected here.
    """
    source: str = ""
    tier: str = "unverified"  # "verified", "inferred", "unverified"
    quality: float = 0.5  # 0.0-1.0
    field_name: str = ""
    value: Any = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Decision-mode constants
# ---------------------------------------------------------------------------

DECISION_MODES = [
    "auto_approve",
    "review_recommended",
    "expert_required",
    "blocked",
    "insufficient_data",
]


# ---------------------------------------------------------------------------
# StructuredConfidence
# ---------------------------------------------------------------------------

@dataclass
class StructuredConfidence:
    overall: float  # 0.0-1.0
    by_dimension: dict  # {dimension_name: confidence}
    data_completeness: float  # fraction of expected evidence actually found
    evidence_tier_distribution: dict  # {tier: count}
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# GapReport
# ---------------------------------------------------------------------------

@dataclass
class GapReport:
    gaps: list[dict]  # [{field, severity, impact, suggestion}]
    total_gaps: int
    critical_gaps: int  # severity == "critical"
    gap_severity_tiers: dict  # {"critical": [...], "major": [...], "minor": [...]}

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# ReviewPackage
# ---------------------------------------------------------------------------

@dataclass
class ReviewPackage:
    ingredient_name: str
    run_id: str
    timestamp: str  # ISO format
    confidence: StructuredConfidence
    gap_report: GapReport
    scorecards: list[dict]  # ScoreCard.to_dict() for each candidate
    scenarios: list[dict]  # Scenario.to_dict() for each consolidation scenario
    recommendations: list[dict]  # Recommendation.to_dict() for each frame
    evidence_summary: dict  # aggregate evidence stats
    decision_modes: list[str]  # available decision modes
    suggested_mode: str  # which mode fits best given the data

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Gap-detection helpers
# ---------------------------------------------------------------------------

# Fields we expect to find evidence for, mapped to severity if missing.
_EXPECTED_FIELDS: list[tuple[str, str, str, str]] = [
    # (field, severity, impact, suggestion)
    ("compliance_target_market", "critical",
     "Cannot verify regulatory status for target market",
     "Obtain regulatory dossier or certification for the target market"),
    ("verified_supplier", "critical",
     "No verified supplier pathway exists",
     "Source at least one supplier with confirmed production capability"),
    ("price_per_kg", "major",
     "Unable to compare landed cost across candidates",
     "Request budgetary quotation from shortlisted suppliers"),
    ("lead_time_weeks", "major",
     "Cannot assess supply-chain timing risk",
     "Collect lead-time estimates from suppliers or freight forwarders"),
    ("single_source_risk", "major",
     "Concentration risk cannot be evaluated",
     "Identify at least one secondary supplier"),
    ("product_form", "minor",
     "Product form info missing; may affect reformulation planning",
     "Confirm physical form (powder, liquid, granule, etc.)"),
    ("evidence_quality_non_critical", "minor",
     "Low evidence quality for non-critical claims",
     "Supplement with additional data sheets or test reports"),
]


def _check_gaps(
    context: dict,
    scorecards: list[ScoreCard],
    evidence_map: dict[str, list],
) -> list[dict]:
    """Return a list of gap dicts for missing or weak evidence."""
    gaps: list[dict] = []

    # Flatten all evidence field names for quick lookup.
    covered_fields: set[str] = set()
    for records in evidence_map.values():
        for rec in records:
            fname = rec.field_name if hasattr(rec, "field_name") else rec.get("field_name", "")
            if fname:
                covered_fields.add(fname)

    for field_name, severity, impact, suggestion in _EXPECTED_FIELDS:
        if field_name not in covered_fields:
            gaps.append({
                "field": field_name,
                "severity": severity,
                "impact": impact,
                "suggestion": suggestion,
            })

    # Check scorecard-level evidence gaps.
    for sc in scorecards:
        if sc.gate_status != "passed":
            for failure in sc.gate_failures:
                gaps.append({
                    "field": f"gate:{failure}:{sc.candidate_name}",
                    "severity": "critical",
                    "impact": f"Hard gate '{failure}' failed for {sc.candidate_name}",
                    "suggestion": f"Resolve gate failure for {sc.candidate_name} before proceeding",
                })

    return gaps


# ---------------------------------------------------------------------------
# ReviewBuilder
# ---------------------------------------------------------------------------

class ReviewBuilder:
    """Assembles a ``ReviewPackage`` from upstream pipeline outputs."""

    def __init__(self) -> None:
        pass  # no dependencies needed

    # -- public API ---------------------------------------------------------

    def build(
        self,
        ingredient_name: str,
        context: dict,
        scorecards: list[ScoreCard],
        scenarios: list[Scenario],
        recommendations: list[Recommendation],
        evidence_map: dict[str, list],
    ) -> ReviewPackage:
        """Build a complete review package for human decision-makers.

        Parameters
        ----------
        ingredient_name:
            The ingredient being evaluated.
        context:
            Dict produced by ``AgnesContext.build()``.
        scorecards:
            List of ``ScoreCard`` objects from scoring step.
        scenarios:
            List of ``Scenario`` objects from consolidation step.
        recommendations:
            List of ``Recommendation`` objects from consolidation step.
        evidence_map:
            ``dict[str, list[EvidenceRecord]]`` keyed by source label.
        """
        confidence = self._compute_confidence(scorecards, evidence_map)
        gap_report = self._compute_gaps(context, scorecards, evidence_map)
        suggested_mode = self._suggest_mode(confidence, gap_report)

        evidence_summary = self._build_evidence_summary(evidence_map)

        return ReviewPackage(
            ingredient_name=ingredient_name,
            run_id=uuid.uuid4().hex[:12],
            timestamp=datetime.utcnow().isoformat(),
            confidence=confidence,
            gap_report=gap_report,
            scorecards=[sc.to_dict() for sc in scorecards],
            scenarios=[s.to_dict() for s in scenarios],
            recommendations=[r.to_dict() for r in recommendations],
            evidence_summary=evidence_summary,
            decision_modes=list(DECISION_MODES),
            suggested_mode=suggested_mode,
        )

    # -- private helpers ----------------------------------------------------

    def _compute_confidence(
        self,
        scorecards: list[ScoreCard],
        evidence_map: dict[str, list],
    ) -> StructuredConfidence:
        """Aggregate confidence from scorecards and evidence quality."""

        # Per-dimension confidence (average across candidates).
        dimension_totals: dict[str, list[float]] = {}
        for sc in scorecards:
            details = sc.dimension_details if hasattr(sc, "dimension_details") else {}
            for dim, info in details.items():
                band = info.get("confidence_band", 0.5) if isinstance(info, dict) else 0.5
                dimension_totals.setdefault(dim, []).append(band)

        by_dimension: dict[str, float] = {
            dim: round(sum(vals) / len(vals), 4)
            for dim, vals in dimension_totals.items()
        } if dimension_totals else {}

        # Evidence tier distribution.
        tier_dist: dict[str, int] = {}
        total_records = 0
        quality_sum = 0.0
        for records in evidence_map.values():
            for rec in records:
                tier = rec.tier if hasattr(rec, "tier") else rec.get("tier", "unverified")
                tier_dist[tier] = tier_dist.get(tier, 0) + 1
                quality_sum += rec.quality if hasattr(rec, "quality") else rec.get("quality", 0.5)
                total_records += 1

        # Data completeness: fraction of expected fields with evidence.
        covered: set[str] = set()
        for records in evidence_map.values():
            for rec in records:
                fname = rec.field_name if hasattr(rec, "field_name") else rec.get("field_name", "")
                if fname:
                    covered.add(fname)
        expected_count = len(_EXPECTED_FIELDS)
        data_completeness = len(covered & {f[0] for f in _EXPECTED_FIELDS}) / expected_count if expected_count else 1.0

        # Overall confidence: blend of scorecard composite confidences,
        # average evidence quality, and data completeness.
        if scorecards:
            avg_composite_conf = sum(
                (sc.composite / 100.0 if hasattr(sc, "composite") else 0.5)
                for sc in scorecards
            ) / len(scorecards)
        else:
            avg_composite_conf = 0.0

        avg_evidence_quality = (quality_sum / total_records) if total_records else 0.0

        overall = round(
            0.45 * avg_composite_conf
            + 0.30 * avg_evidence_quality
            + 0.25 * data_completeness,
            4,
        )
        overall = max(0.0, min(1.0, overall))

        notes: list[str] = []
        if data_completeness < 0.5:
            notes.append("Less than half of expected evidence fields are covered.")
        if avg_evidence_quality < 0.4:
            notes.append("Average evidence quality is low.")
        if tier_dist.get("unverified", 0) > total_records * 0.5 and total_records > 0:
            notes.append("Majority of evidence is unverified.")

        return StructuredConfidence(
            overall=overall,
            by_dimension=by_dimension,
            data_completeness=round(data_completeness, 4),
            evidence_tier_distribution=tier_dist,
            notes=notes,
        )

    def _compute_gaps(
        self,
        context: dict,
        scorecards: list[ScoreCard],
        evidence_map: dict[str, list],
    ) -> GapReport:
        """Identify and categorise data gaps."""
        gaps = _check_gaps(context, scorecards, evidence_map)

        severity_tiers: dict[str, list[dict]] = {
            "critical": [],
            "major": [],
            "minor": [],
        }
        for gap in gaps:
            tier_key = gap["severity"]
            if tier_key in severity_tiers:
                severity_tiers[tier_key].append(gap)
            else:
                severity_tiers.setdefault(tier_key, []).append(gap)

        return GapReport(
            gaps=gaps,
            total_gaps=len(gaps),
            critical_gaps=len(severity_tiers["critical"]),
            gap_severity_tiers=severity_tiers,
        )

    def _suggest_mode(
        self,
        confidence: StructuredConfidence,
        gaps: GapReport,
    ) -> str:
        """Choose the most appropriate decision mode.

        Priority (evaluated top-to-bottom, first match wins):
            1. blocked            — any hard gate failure among gaps
            2. insufficient_data  — evidence quality very low or too many gaps
            3. expert_required    — critical gaps or low confidence
            4. auto_approve       — high confidence, no critical gaps
            5. review_recommended — everything else
        """
        # 1. Blocked: hard gate failures surface as critical gaps starting
        #    with "gate:".
        has_gate_failure = any(
            g["field"].startswith("gate:") for g in gaps.gaps
        )
        if has_gate_failure:
            return "blocked"

        # 2. Insufficient data.
        avg_quality = confidence.overall  # proxy
        if confidence.data_completeness < 0.3 or avg_quality < 0.3:
            return "insufficient_data"

        # 3. Expert required.
        if gaps.critical_gaps > 0 or confidence.overall < 0.65:
            return "expert_required"

        # 4. Auto approve.
        if confidence.overall >= 0.85 and gaps.critical_gaps == 0:
            return "auto_approve"

        # 5. Fallback.
        return "review_recommended"

    # -- evidence summary ---------------------------------------------------

    @staticmethod
    def _build_evidence_summary(evidence_map: dict[str, list]) -> dict:
        """Compute aggregate statistics over the evidence map."""
        total = 0
        quality_sum = 0.0
        tier_counts: dict[str, int] = {}
        sources: set[str] = set()

        for source_label, records in evidence_map.items():
            sources.add(source_label)
            for rec in records:
                total += 1
                quality_sum += rec.quality if hasattr(rec, "quality") else rec.get("quality", 0.5)
                tier = rec.tier if hasattr(rec, "tier") else rec.get("tier", "unverified")
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        return {
            "total_records": total,
            "unique_sources": len(sources),
            "average_quality": round(quality_sum / total, 4) if total else 0.0,
            "tier_counts": tier_counts,
        }
