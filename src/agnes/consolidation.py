"""
Step 6 — Consolidation & Recommendation.

Builds 5 scenario types from scored supplier candidates, computes 6 metrics
per scenario, then applies 3 frame-specific ranking policies to produce
reproducible recommendations.

Scenario types:
    1. Full Consolidation
    2. Dual-Supplier Resilient
    3. Phased Migration
    4. Segmented by Certification
    5. Segmented by Product Form

Scoring metrics (per scenario):
    1. supplier_reduction_count  — how many current suppliers are eliminated
    2. demand_footprint          — BOM coverage proxy from centrality scores
    3. compliance_coverage       — % of companies whose compliance reqs are met
    4. transition_complexity     — inverted for scoring (low complexity = high)
    5. resilience_risk           — concentration risk assessment
    6. scenario_confidence       — composite from evidence quality + gap count

Recommendation frames:
    best_cost       — cost/consolidation optimised (resilience-gated)
    best_low_risk   — minimal disruption / maximum resilience
    best_balanced   — equal weight across all 6 metrics
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..procurement.cpg_db import CpgDatabase


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    scenario_type: str
    scenario_id: str
    metrics: dict  # {metric_name: float}  — all values 0.0-1.0
    supplier_assignments: dict  # {company_id: supplier_name}
    company_exclusions: list[dict]  # [{company, blocking_reason, possible_fallback}]
    confidence_fields: dict  # raw constituents of scenario_confidence

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    frame: str  # "best_cost" | "best_low_risk" | "best_balanced"
    recommended_scenario: Scenario
    ranking: list[tuple]  # [(scenario_id, weighted_score)]
    policy_description: str

    def to_dict(self) -> dict:
        return {
            "frame": self.frame,
            "recommended_scenario": self.recommended_scenario.to_dict(),
            "ranking": [
                {"scenario_id": sid, "weighted_score": round(ws, 6)}
                for sid, ws in self.ranking
            ],
            "policy_description": self.policy_description,
        }


# ---------------------------------------------------------------------------
# Ranking-policy definitions (P2 fix — explicit, reproducible)
# ---------------------------------------------------------------------------

# Weights are (metric_name, weight) tuples.  Each policy function receives a
# Scenario and returns a float score.

_BEST_COST_WEIGHTS: list[tuple[str, float]] = [
    ("supplier_reduction_count", 0.40),
    ("demand_footprint", 0.40),
    ("transition_complexity_inv", 0.20),
]

_BEST_LOW_RISK_WEIGHTS: list[tuple[str, float]] = [
    ("resilience_risk_inv", 0.40),
    ("transition_complexity_inv", 0.30),
    ("scenario_confidence", 0.30),
]

# All 6 normalised metrics equally weighted (1/6 each).
_BEST_BALANCED_WEIGHTS: list[tuple[str, float]] = [
    ("supplier_reduction_count", 1 / 6),
    ("demand_footprint", 1 / 6),
    ("compliance_coverage", 1 / 6),
    ("transition_complexity_inv", 1 / 6),
    ("resilience_risk_inv", 1 / 6),
    ("scenario_confidence", 1 / 6),
]


def _derived_metrics(m: dict) -> dict:
    """Expand raw metrics dict with inverted variants used by ranking policies."""
    out = dict(m)
    out["transition_complexity_inv"] = 1.0 - m.get("transition_complexity", 0.5)
    out["resilience_risk_inv"] = 1.0 - m.get("resilience_risk", 0.5)
    return out


def _weighted_score(scenario: Scenario, weights: list[tuple[str, float]]) -> float:
    dm = _derived_metrics(scenario.metrics)
    return sum(dm.get(name, 0.0) * w for name, w in weights)


def _is_resilience_acceptable(scenario: Scenario, threshold: float = 0.60) -> bool:
    """A scenario's resilience risk is acceptable when the raw risk is below threshold."""
    return scenario.metrics.get("resilience_risk", 1.0) < threshold


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _supplier_reduction_count(
    current_supplier_count: int,
    scenario_supplier_count: int,
) -> float:
    """Normalised 0-1: fraction of current suppliers eliminated."""
    if current_supplier_count <= 0:
        return 0.0
    eliminated = max(current_supplier_count - scenario_supplier_count, 0)
    return _clamp(eliminated / current_supplier_count)


def _demand_footprint(
    covered_centrality: float,
    total_centrality: float,
) -> float:
    """BOM coverage proxy from centrality scores (NOT volume)."""
    if total_centrality <= 0:
        return 0.0
    return _clamp(covered_centrality / total_centrality)


def _compliance_coverage(
    compliant_company_count: int,
    total_company_count: int,
) -> float:
    if total_company_count <= 0:
        return 0.0
    return _clamp(compliant_company_count / total_company_count)


def _transition_complexity(
    reassignment_count: int,
    total_assignments: int,
    has_certification_gap: bool = False,
    phased: bool = False,
) -> float:
    """0 = trivial, 1 = maximally complex."""
    if total_assignments <= 0:
        return 0.0
    base = _clamp(reassignment_count / total_assignments)
    if has_certification_gap:
        base = min(base + 0.15, 1.0)
    if phased:
        base = max(base - 0.10, 0.0)
    return _clamp(base)


def _resilience_risk(supplier_count: int, max_share: float) -> float:
    """Concentration risk: 0 = fully diversified, 1 = single point of failure."""
    if supplier_count <= 0:
        return 1.0
    if supplier_count == 1:
        return 0.95
    # max_share is the fraction of demand held by the largest supplier
    concentration = _clamp(max_share)
    diversity_bonus = _clamp(1.0 - 1.0 / supplier_count)
    return _clamp(1.0 - 0.5 * (1.0 - concentration) - 0.5 * diversity_bonus)


def _scenario_confidence(
    evidence_quality: float,
    gap_count: int,
    max_gaps: int = 10,
) -> float:
    """Composite from evidence quality (0-1) and data-gap penalty."""
    gap_penalty = _clamp(gap_count / max(max_gaps, 1))
    return _clamp(0.65 * evidence_quality + 0.35 * (1.0 - gap_penalty))


# ---------------------------------------------------------------------------
# Scenario builder helpers
# ---------------------------------------------------------------------------

def _make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _pick_top_supplier(scored_candidates: list, n: int = 1) -> list[dict]:
    """Return top-n candidates sorted by composite_score descending."""
    by_score = sorted(
        scored_candidates,
        key=lambda c: c.get("composite_score", c.get("consolidation_score", 0)),
        reverse=True,
    )
    return by_score[:n]


def _candidate_name(c: dict) -> str:
    return c.get("name", c.get("supplier", "unknown"))


def _candidate_certs(c: dict) -> list[str]:
    certs = c.get("certifications", [])
    if isinstance(certs, str):
        certs = [x.strip() for x in certs.split(",")]
    return [x.lower() for x in certs]


def _candidate_product_forms(c: dict) -> list[str]:
    forms = c.get("product_forms", c.get("products_offered", []))
    if isinstance(forms, str):
        forms = [x.strip() for x in forms.split(",")]
    return forms


def _centrality_for_company(company: str, context: dict) -> float:
    """Look up the BOM centrality score for a company from pipeline context."""
    centrality_map: dict = context.get("centrality_scores", {})
    return centrality_map.get(company, 1.0)


def _company_compliance_met(company: str, supplier: dict, context: dict) -> bool:
    """Check whether the supplier satisfies the company's compliance needs."""
    required = context.get("company_compliance", {}).get(company, [])
    if not required:
        return True  # no specific requirement recorded
    sup_certs = _candidate_certs(supplier)
    sup_text = " ".join(sup_certs) + " " + supplier.get("snippet", "")
    return all(req.lower() in sup_text for req in required)


# ---------------------------------------------------------------------------
# ConsolidationModeler
# ---------------------------------------------------------------------------

class ConsolidationModeler:
    """
    Builds 5 scenario types and produces 3 frame-ranked recommendations.

    Parameters
    ----------
    cpg_db : CpgDatabase
        Database handle for company / supplier / BOM look-ups.
    """

    def __init__(self, cpg_db: "CpgDatabase"):
        self.db = cpg_db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def model_scenarios(
        self,
        context: dict,
        scored_candidates: list[dict],
    ) -> list[Scenario]:
        """
        Build all 5 scenario types from feasible candidates.

        Parameters
        ----------
        context : dict
            Pipeline context with keys such as:
              - companies          : list[str]  — participating company names
              - current_suppliers  : list[str]  — incumbent supplier names
              - centrality_scores  : dict[str, float]  — company -> centrality
              - company_compliance : dict[str, list[str]]  — company -> required certs
              - evidence_quality   : float  — 0-1 aggregate evidence quality
              - gap_count          : int    — number of data gaps detected
        scored_candidates : list[dict]
            Supplier candidates already scored by the ranking engine (Step 5).
        """
        if not scored_candidates:
            return []

        companies: list[str] = context.get("companies", [])
        current_suppliers: list[str] = context.get("current_suppliers", [])
        current_count = len(set(current_suppliers)) if current_suppliers else 1

        scenarios: list[Scenario] = []

        scenarios.append(
            self._build_full_consolidation(context, scored_candidates, companies, current_count)
        )
        scenarios.append(
            self._build_dual_supplier(context, scored_candidates, companies, current_count)
        )
        scenarios.append(
            self._build_phased_migration(context, scored_candidates, companies, current_count)
        )
        scenarios.append(
            self._build_segmented_certification(context, scored_candidates, companies, current_count)
        )
        scenarios.append(
            self._build_segmented_product_form(context, scored_candidates, companies, current_count)
        )

        return scenarios

    def recommend(self, scenarios: list[Scenario]) -> list[Recommendation]:
        """
        Apply 3 frame-specific ranking policies.

        Returns one Recommendation per frame, each containing the winning
        scenario and the full ranking with scores.
        """
        if not scenarios:
            return []

        recommendations: list[Recommendation] = []

        # --- Best cost / consolidation frame ---
        # Only consider scenarios where resilience risk is acceptable.
        resilience_ok = [s for s in scenarios if _is_resilience_acceptable(s)]
        pool_cost = resilience_ok if resilience_ok else scenarios  # fallback
        ranked_cost = sorted(
            [(s.scenario_id, _weighted_score(s, _BEST_COST_WEIGHTS), s) for s in pool_cost],
            key=lambda t: (-t[1], t[0]),  # descending score, then id for stability
        )
        recommendations.append(Recommendation(
            frame="best_cost",
            recommended_scenario=ranked_cost[0][2],
            ranking=[(sid, sc) for sid, sc, _ in ranked_cost],
            policy_description=(
                "Ranks by supplier_reduction_count (40%) + demand_footprint_covered (40%) "
                "+ transition_complexity_inverted (20%). Applied only among scenarios where "
                "resilience_risk is flagged as acceptable (raw risk < 0.60)."
            ),
        ))

        # --- Best low-risk frame ---
        ranked_risk = sorted(
            [(s.scenario_id, _weighted_score(s, _BEST_LOW_RISK_WEIGHTS), s) for s in scenarios],
            key=lambda t: (-t[1], t[0]),
        )
        recommendations.append(Recommendation(
            frame="best_low_risk",
            recommended_scenario=ranked_risk[0][2],
            ranking=[(sid, sc) for sid, sc, _ in ranked_risk],
            policy_description=(
                "Ranks by resilience_risk_score_inverted (40%) + "
                "transition_complexity_inverted (30%) + scenario_confidence (30%)."
            ),
        ))

        # --- Best balanced frame ---
        # Equal weight across all 6 metrics, with scenario_confidence as tiebreaker.
        ranked_balanced = sorted(
            [
                (
                    s.scenario_id,
                    _weighted_score(s, _BEST_BALANCED_WEIGHTS),
                    _derived_metrics(s.metrics).get("scenario_confidence", 0.0),
                    s,
                )
                for s in scenarios
            ],
            key=lambda t: (-t[1], -t[2], t[0]),  # score desc, confidence desc, id asc
        )
        recommendations.append(Recommendation(
            frame="best_balanced",
            recommended_scenario=ranked_balanced[0][3],
            ranking=[(sid, sc) for sid, sc, _, _ in ranked_balanced],
            policy_description=(
                "Equal weight (1/6) across all 6 metrics after normalisation, "
                "with scenario_confidence as tiebreaker."
            ),
        ))

        return recommendations

    # ------------------------------------------------------------------
    # Scenario builders (private)
    # ------------------------------------------------------------------

    def _compute_metrics(
        self,
        context: dict,
        companies: list[str],
        assignments: dict[str, str],
        exclusions: list[dict],
        unique_suppliers: set[str],
        current_count: int,
        phased: bool = False,
        has_cert_gap: bool = False,
    ) -> tuple[dict, dict]:
        """Return (metrics_dict, confidence_fields_dict)."""
        assigned_companies = set(assignments.keys())
        excluded_companies = {e["company"] for e in exclusions}
        all_companies = set(companies)

        # --- supplier_reduction_count ---
        src = _supplier_reduction_count(current_count, len(unique_suppliers))

        # --- demand_footprint ---
        total_centrality = sum(_centrality_for_company(c, context) for c in all_companies)
        covered_centrality = sum(_centrality_for_company(c, context) for c in assigned_companies)
        df = _demand_footprint(covered_centrality, total_centrality)

        # --- compliance_coverage ---
        # Build a supplier-dict lookup by name from scored_candidates in context
        candidate_lookup: dict[str, dict] = {}
        for cand in context.get("scored_candidates", []):
            candidate_lookup[_candidate_name(cand)] = cand

        compliant = 0
        for company, sup_name in assignments.items():
            sup_dict = candidate_lookup.get(sup_name, {})
            if _company_compliance_met(company, sup_dict, context):
                compliant += 1
        cc = _compliance_coverage(compliant, len(all_companies))

        # --- transition_complexity ---
        # Reassignment = any company being moved to a new supplier
        current_map: dict[str, str] = context.get("current_assignments", {})
        reassigned = sum(
            1 for c, s in assignments.items()
            if current_map.get(c, "") != s
        )
        tc = _transition_complexity(
            reassigned, len(all_companies),
            has_certification_gap=has_cert_gap,
            phased=phased,
        )

        # --- resilience_risk ---
        if unique_suppliers:
            supplier_shares: dict[str, int] = {}
            for sup in assignments.values():
                supplier_shares[sup] = supplier_shares.get(sup, 0) + 1
            total_assigned = max(sum(supplier_shares.values()), 1)
            max_share = max(supplier_shares.values()) / total_assigned
        else:
            max_share = 1.0
        rr = _resilience_risk(len(unique_suppliers), max_share)

        # --- scenario_confidence ---
        ev_quality = context.get("evidence_quality", 0.5)
        gap_count = context.get("gap_count", 0)
        sc = _scenario_confidence(ev_quality, gap_count)

        metrics = {
            "supplier_reduction_count": round(src, 4),
            "demand_footprint": round(df, 4),
            "compliance_coverage": round(cc, 4),
            "transition_complexity": round(tc, 4),
            "resilience_risk": round(rr, 4),
            "scenario_confidence": round(sc, 4),
        }
        confidence_fields = {
            "evidence_quality": ev_quality,
            "gap_count": gap_count,
            "exclusion_count": len(exclusions),
            "assigned_company_count": len(assigned_companies),
            "total_company_count": len(all_companies),
        }
        return metrics, confidence_fields

    # -- 1. Full Consolidation -------------------------------------------------

    def _build_full_consolidation(
        self, context: dict, scored: list[dict], companies: list[str], current_count: int,
    ) -> Scenario:
        top = _pick_top_supplier(scored, 1)
        supplier = top[0] if top else {}
        sup_name = _candidate_name(supplier)

        assignments: dict[str, str] = {}
        exclusions: list[dict] = []

        for company in companies:
            if _company_compliance_met(company, supplier, context):
                assignments[company] = sup_name
            else:
                exclusions.append({
                    "company": company,
                    "blocking_reason": "Compliance requirements not met by consolidated supplier",
                    "possible_fallback": _find_fallback(company, scored, context),
                })

        metrics, conf = self._compute_metrics(
            context, companies, assignments, exclusions,
            unique_suppliers={sup_name}, current_count=current_count,
        )
        # Stash scored_candidates in context so _compute_metrics can use them
        context.setdefault("scored_candidates", scored)

        return Scenario(
            scenario_type="full_consolidation",
            scenario_id=_make_id("full"),
            metrics=metrics,
            supplier_assignments=assignments,
            company_exclusions=exclusions,
            confidence_fields=conf,
        )

    # -- 2. Dual-Supplier Resilient --------------------------------------------

    def _build_dual_supplier(
        self, context: dict, scored: list[dict], companies: list[str], current_count: int,
    ) -> Scenario:
        context.setdefault("scored_candidates", scored)
        top2 = _pick_top_supplier(scored, 2)
        primary = top2[0] if len(top2) >= 1 else {}
        secondary = top2[1] if len(top2) >= 2 else primary

        pri_name = _candidate_name(primary)
        sec_name = _candidate_name(secondary)

        assignments: dict[str, str] = {}
        exclusions: list[dict] = []

        # Split: ~60 % primary, ~40 % secondary (round-robin by index)
        for i, company in enumerate(companies):
            preferred = primary if i % 5 < 3 else secondary
            pref_name = _candidate_name(preferred)
            if _company_compliance_met(company, preferred, context):
                assignments[company] = pref_name
            else:
                # Try the other supplier
                alt = secondary if preferred is primary else primary
                alt_name = _candidate_name(alt)
                if _company_compliance_met(company, alt, context):
                    assignments[company] = alt_name
                else:
                    exclusions.append({
                        "company": company,
                        "blocking_reason": "Neither primary nor secondary supplier meets compliance",
                        "possible_fallback": _find_fallback(company, scored, context),
                    })

        unique = {pri_name, sec_name} if pri_name != sec_name else {pri_name}
        metrics, conf = self._compute_metrics(
            context, companies, assignments, exclusions,
            unique_suppliers=unique, current_count=current_count,
        )

        return Scenario(
            scenario_type="dual_supplier_resilient",
            scenario_id=_make_id("dual"),
            metrics=metrics,
            supplier_assignments=assignments,
            company_exclusions=exclusions,
            confidence_fields=conf,
        )

    # -- 3. Phased Migration ---------------------------------------------------

    def _build_phased_migration(
        self, context: dict, scored: list[dict], companies: list[str], current_count: int,
    ) -> Scenario:
        context.setdefault("scored_candidates", scored)
        top = _pick_top_supplier(scored, 1)
        supplier = top[0] if top else {}
        sup_name = _candidate_name(supplier)

        # Phase 1: high-confidence companies (compliance met, simple transition)
        phase1: dict[str, str] = {}
        phase2: dict[str, str] = {}
        exclusions: list[dict] = []

        current_map: dict[str, str] = context.get("current_assignments", {})

        for company in companies:
            if not _company_compliance_met(company, supplier, context):
                exclusions.append({
                    "company": company,
                    "blocking_reason": "Compliance gap — deferred to later phase or excluded",
                    "possible_fallback": _find_fallback(company, scored, context),
                })
                continue
            # Companies already with this supplier or with straightforward transition
            if current_map.get(company) == sup_name:
                phase1[company] = sup_name
            elif _centrality_for_company(company, context) >= 1.0:
                # High-centrality companies migrated first for impact
                phase1[company] = sup_name
            else:
                phase2[company] = sup_name

        assignments = {**phase1, **phase2}
        metrics, conf = self._compute_metrics(
            context, companies, assignments, exclusions,
            unique_suppliers={sup_name}, current_count=current_count,
            phased=True,
        )

        return Scenario(
            scenario_type="phased_migration",
            scenario_id=_make_id("phased"),
            metrics=metrics,
            supplier_assignments=assignments,
            company_exclusions=exclusions,
            confidence_fields=conf,
        )

    # -- 4. Segmented by Certification -----------------------------------------

    def _build_segmented_certification(
        self, context: dict, scored: list[dict], companies: list[str], current_count: int,
    ) -> Scenario:
        context.setdefault("scored_candidates", scored)

        # Group companies by their primary certification requirement
        company_compliance: dict[str, list[str]] = context.get("company_compliance", {})
        cert_groups: dict[str, list[str]] = {}
        for company in companies:
            reqs = company_compliance.get(company, [])
            key = ",".join(sorted(r.lower() for r in reqs)) if reqs else "_none"
            cert_groups.setdefault(key, []).append(company)

        assignments: dict[str, str] = {}
        exclusions: list[dict] = []
        unique_suppliers: set[str] = set()
        has_cert_gap = False

        for cert_key, group_companies in cert_groups.items():
            # Find best supplier for this certification set
            best = None
            for cand in sorted(
                scored,
                key=lambda c: c.get("composite_score", c.get("consolidation_score", 0)),
                reverse=True,
            ):
                if all(
                    _company_compliance_met(co, cand, context) for co in group_companies
                ):
                    best = cand
                    break

            if best:
                bname = _candidate_name(best)
                unique_suppliers.add(bname)
                for co in group_companies:
                    assignments[co] = bname
            else:
                has_cert_gap = True
                for co in group_companies:
                    exclusions.append({
                        "company": co,
                        "blocking_reason": f"No supplier meets certification requirement '{cert_key}'",
                        "possible_fallback": _find_fallback(co, scored, context),
                    })

        metrics, conf = self._compute_metrics(
            context, companies, assignments, exclusions,
            unique_suppliers=unique_suppliers, current_count=current_count,
            has_cert_gap=has_cert_gap,
        )

        return Scenario(
            scenario_type="segmented_by_certification",
            scenario_id=_make_id("cert"),
            metrics=metrics,
            supplier_assignments=assignments,
            company_exclusions=exclusions,
            confidence_fields=conf,
        )

    # -- 5. Segmented by Product Form ------------------------------------------

    def _build_segmented_product_form(
        self, context: dict, scored: list[dict], companies: list[str], current_count: int,
    ) -> Scenario:
        context.setdefault("scored_candidates", scored)

        # Group companies by the product form they require
        form_map: dict[str, str] = context.get("company_product_forms", {})
        form_groups: dict[str, list[str]] = {}
        for company in companies:
            form = form_map.get(company, "_default")
            form_groups.setdefault(form, []).append(company)

        assignments: dict[str, str] = {}
        exclusions: list[dict] = []
        unique_suppliers: set[str] = set()

        for form_key, group_companies in form_groups.items():
            # Prefer suppliers that list this product form
            best = None
            for cand in sorted(
                scored,
                key=lambda c: c.get("composite_score", c.get("consolidation_score", 0)),
                reverse=True,
            ):
                cand_forms = _candidate_product_forms(cand)
                # If form is default or supplier lists the form (or has no form info)
                if (
                    form_key == "_default"
                    or not cand_forms
                    or any(form_key.lower() in f.lower() for f in cand_forms)
                ):
                    if _company_compliance_met(group_companies[0], cand, context):
                        best = cand
                        break

            if best:
                bname = _candidate_name(best)
                unique_suppliers.add(bname)
                for co in group_companies:
                    if _company_compliance_met(co, best, context):
                        assignments[co] = bname
                    else:
                        exclusions.append({
                            "company": co,
                            "blocking_reason": f"Supplier for form '{form_key}' does not meet compliance",
                            "possible_fallback": _find_fallback(co, scored, context),
                        })
            else:
                for co in group_companies:
                    exclusions.append({
                        "company": co,
                        "blocking_reason": f"No supplier offers product form '{form_key}'",
                        "possible_fallback": _find_fallback(co, scored, context),
                    })

        metrics, conf = self._compute_metrics(
            context, companies, assignments, exclusions,
            unique_suppliers=unique_suppliers, current_count=current_count,
        )

        return Scenario(
            scenario_type="segmented_by_product_form",
            scenario_id=_make_id("form"),
            metrics=metrics,
            supplier_assignments=assignments,
            company_exclusions=exclusions,
            confidence_fields=conf,
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _find_fallback(
    company: str,
    scored_candidates: list[dict],
    context: dict,
) -> str:
    """Return the name of the best alternative supplier that meets compliance for this company."""
    for cand in sorted(
        scored_candidates,
        key=lambda c: c.get("composite_score", c.get("consolidation_score", 0)),
        reverse=True,
    ):
        if _company_compliance_met(company, cand, context):
            return _candidate_name(cand)
    return "none_found"
