"""
Agnes CPG Pipeline -- Step 5: Feasibility Scoring.

Scores each substitution candidate across 4 dimensions after applying
4 hard gates (P2 Fix 3 -- concrete thresholds).

Hard Gates (all must pass for gate_status = "passed"):
  Gate 1 -- Compliance floor: verified regulatory_status from
            regulatory_database per target market, OR verified
            certification from certification_body.
  Gate 2 -- Functional floor: verified technical_property from
            supplier_spec or regulatory_database, OR two
            non-conflicting inferred claims from different sources.
  Gate 3 -- Supply availability: at least 1 supplier in the database.
  Gate 4 -- Safety: no unresolved safety flags (allergen conflicts,
            banned substance matches).

Scoring Dimensions (0-100, higher is better):
  1. Functional Fit   -- functional role match, BOM co-occurrence, name similarity
  2. Compliance Fit   -- regulatory readiness for target market
  3. Supply Viability -- supplier count, single-source risk, supplier scores
  4. Operational Fit  -- transition ease, variant proximity, BOM compatibility
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Optional, TYPE_CHECKING

from src.procurement.ranking import score_compliance, score_quality
from src.procurement.substitution_engine import FUNCTIONAL_CATEGORIES

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase


# ---------------------------------------------------------------------------
# ScoreCard dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoreCard:
    """Result of scoring a single substitution candidate."""

    candidate_name: str
    gate_status: str                    # "passed" | "blocked"
    gate_failures: list[str]            # which gates failed
    functional_fit: float               # 0-100
    compliance_fit: float               # 0-100
    supply_viability: float             # 0-100
    operational_fit: float              # 0-100
    composite: float                    # weighted average
    dimension_details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Default dimension weights
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "functional_fit": 0.30,
    "compliance_fit": 0.30,
    "supply_viability": 0.20,
    "operational_fit": 0.20,
}


# ---------------------------------------------------------------------------
# Functional-category lookup helper
# ---------------------------------------------------------------------------

_CATEGORY_INDEX: dict[str, str] | None = None


def _get_category_index() -> dict[str, str]:
    """Build a lazy mapping from ingredient fragment -> category name."""
    global _CATEGORY_INDEX
    if _CATEGORY_INDEX is not None:
        return _CATEGORY_INDEX
    idx: dict[str, str] = {}
    for cat, members in FUNCTIONAL_CATEGORIES.items():
        for member in members:
            idx[member.lower()] = cat
    _CATEGORY_INDEX = idx
    return idx


def _category_for(ingredient: str) -> str | None:
    """Return the functional category for an ingredient, or None."""
    idx = _get_category_index()
    ing = ingredient.lower().strip()
    if ing in idx:
        return idx[ing]
    for fragment, cat in idx.items():
        if fragment in ing or ing in fragment:
            return cat
    return None


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------

def _evidence_list(evidence: Any) -> list[dict]:
    """Normalise evidence to a flat list of dicts."""
    if isinstance(evidence, list):
        return evidence
    if isinstance(evidence, dict):
        claims = evidence.get("claims", [])
        if isinstance(claims, list):
            return claims
        if isinstance(claims, dict):
            flat: list[dict] = []
            for v in claims.values():
                if isinstance(v, list):
                    flat.extend(v)
            return flat
    return []


def _filter_evidence(
    evidence_items: list[dict],
    *,
    claim_type: str | None = None,
    source: str | None = None,
    verified: bool | None = None,
) -> list[dict]:
    """Filter evidence items by optional criteria."""
    result = []
    for e in evidence_items:
        if claim_type is not None:
            ct = e.get("claim_type", e.get("type", ""))
            if ct != claim_type:
                continue
        if source is not None:
            src = e.get("source", e.get("source_type", e.get("source_tier", "")))
            if src != source:
                continue
        if verified is not None:
            v = e.get("verified", e.get("verification_status", "") == "verified")
            if bool(v) != verified:
                continue
        result.append(e)
    return result


def _is_verified(e: dict) -> bool:
    """Check whether an evidence item is verified."""
    if "verified" in e:
        return bool(e["verified"])
    return e.get("verification_status", e.get("status", "")) == "verified"


def _is_inferred(e: dict) -> bool:
    """Check whether an evidence item is inferred."""
    status = e.get("verification_status", e.get("status", ""))
    return status == "inferred"


def _source_of(e: dict) -> str:
    """Extract source identifier from an evidence item."""
    return e.get("source", e.get("source_type", e.get("source_tier", "unknown")))


def _claims_conflict(a: dict, b: dict) -> bool:
    """Two inferred claims conflict if they assert opposite values for the same property."""
    prop_a = a.get("property", a.get("claim", ""))
    prop_b = b.get("property", b.get("claim", ""))
    if prop_a != prop_b:
        return False
    val_a = a.get("value")
    val_b = b.get("value")
    return val_a is not None and val_b is not None and val_a != val_b


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------

def _cand_name(candidate: Any) -> str:
    """Extract canonical name from a Candidate dataclass or dict."""
    if hasattr(candidate, "canonical_name"):
        return candidate.canonical_name
    if isinstance(candidate, dict):
        return candidate.get("canonical_name", candidate.get("name", candidate.get("ingredient", "unknown")))
    return str(candidate)


def _cand_type(candidate: Any) -> str:
    """Extract candidate_type from a Candidate dataclass or dict."""
    if hasattr(candidate, "candidate_type"):
        return candidate.candidate_type
    if isinstance(candidate, dict):
        return candidate.get("candidate_type", candidate.get("type", "broader_functional"))
    return "broader_functional"


def _cand_signals(candidate: Any) -> dict:
    """Extract signals dict from a Candidate dataclass or dict."""
    if hasattr(candidate, "signals"):
        return candidate.signals or {}
    if isinstance(candidate, dict):
        return candidate.get("signals", {})
    return {}


def _cand_product_ids(candidate: Any) -> list:
    """Extract product_ids from a Candidate dataclass or dict."""
    if hasattr(candidate, "product_ids"):
        return candidate.product_ids or []
    if isinstance(candidate, dict):
        return candidate.get("product_ids", [])
    return []


def _cand_attrs(candidate: Any) -> dict:
    """Extract attributes from a Candidate dataclass or dict."""
    if isinstance(candidate, dict):
        return candidate.get("attributes", {})
    return {}


# ---------------------------------------------------------------------------
# Gate 1: Compliance floor
# ---------------------------------------------------------------------------

def _gate_compliance_floor(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
) -> tuple[bool, str]:
    """
    Gate 1 -- Compliance floor.

    Candidate must have at least one verified regulatory_status from
    regulatory_database per target market, OR at least one verified
    certification from a certification_body.
    """
    compliance_ctx = context.get("compliance", {})
    required_markets = (
        compliance_ctx.get("required_markets", [])
        or compliance_ctx.get("markets", [])
        or context.get("required_markets", [])
    )

    # Verified regulatory_status from regulatory_database
    reg_verified = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("regulatory_status", "regulatory")
        and _is_verified(e)
        and _source_of(e) in ("regulatory_database", "regulatory_db")
    ]

    # Verified certification from certification_body
    cert_verified = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("certification", "cert")
        and _is_verified(e)
        and _source_of(e) in ("certification_body", "cert_body")
    ]

    if not required_markets:
        # No specific markets required -- pass if we have any verified reg or cert
        if reg_verified or cert_verified:
            return True, "Compliance floor met (no specific market requirements)"
        return False, "No verified regulatory status or certification found"

    # Check per market
    uncovered: list[str] = []
    for market in required_markets:
        market_lower = market.lower()

        has_reg = any(
            market_lower in (
                e.get("market", "") + " " + e.get("scope", "") + " " + e.get("value", "")
            ).lower()
            for e in reg_verified
        )
        has_cert = any(
            market_lower in (
                e.get("scope", "") + " " + e.get("value", "") + " " + e.get("market", "")
            ).lower()
            for e in cert_verified
        )

        if not has_reg and not has_cert:
            uncovered.append(market)

    if uncovered:
        return False, f"No verified regulatory coverage for market(s): {', '.join(uncovered)}"
    return True, "Compliance floor met for all required markets"


# ---------------------------------------------------------------------------
# Gate 2: Functional floor
# ---------------------------------------------------------------------------

def _gate_functional_floor(
    candidate: Any,
    evidence_items: list[dict],
) -> tuple[bool, str]:
    """
    Gate 2 -- Functional floor.

    At least one verified technical_property from supplier_spec or
    regulatory_database, OR two non-conflicting inferred claims from
    different sources.
    """
    tech_claims = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("technical_property", "technical")
    ]

    # Path A: verified from authoritative source
    authoritative_sources = {"supplier_spec", "regulatory_database", "regulatory_db"}
    verified_auth = [
        e for e in tech_claims
        if _is_verified(e) and _source_of(e) in authoritative_sources
    ]
    if verified_auth:
        return True, (
            f"Functional floor met: {len(verified_auth)} verified technical "
            f"claim(s) from authoritative source(s)"
        )

    # Path B: two non-conflicting inferred claims from different sources
    inferred = [e for e in tech_claims if _is_inferred(e)]
    if len(inferred) >= 2:
        sources = {_source_of(e) for e in inferred}
        if len(sources) >= 2:
            has_conflict = False
            for i in range(len(inferred)):
                for j in range(i + 1, len(inferred)):
                    if _claims_conflict(inferred[i], inferred[j]):
                        has_conflict = True
                        break
                if has_conflict:
                    break
            if not has_conflict:
                return True, (
                    f"Functional floor met: {len(inferred)} non-conflicting "
                    f"inferred claims from {len(sources)} source types"
                )
            return False, "Conflicting inferred technical property claims"

    return False, (
        "Insufficient technical evidence: need 1 verified claim from "
        "supplier_spec/regulatory_database, or 2 non-conflicting inferred "
        "claims from different source types"
    )


# ---------------------------------------------------------------------------
# Gate 3: Supply availability
# ---------------------------------------------------------------------------

def _gate_supply_availability(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
    cpg_db: Optional["CpgDatabase"] = None,
) -> tuple[bool, str]:
    """
    Gate 3 -- Supply availability.

    Candidate must have at least 1 supplier in the database.
    """
    name = _cand_name(candidate)
    supplier_count = 0

    # Check evidence for supplier_linkage claims
    linkage_claims = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("supplier_linkage", "supplier")
    ]
    supplier_names: set[str] = set()
    for e in linkage_claims:
        s = e.get("supplier_name", e.get("supplier", ""))
        if s:
            supplier_names.add(s.lower())

    # Check CpgDatabase
    if cpg_db and name:
        try:
            search_results = cpg_db.search_ingredients(name, limit=3)
            for sr in search_results:
                for pid in sr.get("product_ids", []):
                    suppliers = cpg_db.get_suppliers_for_product(pid)
                    for s in suppliers:
                        sn = s.get("Name", s.get("name", ""))
                        if sn:
                            supplier_names.add(sn.lower())
        except Exception:
            pass

    supplier_count = len(supplier_names)

    # Also check supply context
    supply_ctx = context.get("supply", {})
    if isinstance(supply_ctx, dict):
        ctx_suppliers = supply_ctx.get("supplier_count", 0)
        supplier_count = max(supplier_count, ctx_suppliers)

    if supplier_count >= 1:
        return True, f"Supply gate passed: {supplier_count} supplier(s) available"
    return False, "No suppliers found in database for this candidate"


# ---------------------------------------------------------------------------
# Gate 4: Safety
# ---------------------------------------------------------------------------

def _gate_safety(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
) -> tuple[bool, str]:
    """
    Gate 4 -- Safety.

    No unresolved safety flags: allergen conflicts, banned substance matches.
    """
    failures: list[str] = []
    name = _cand_name(candidate)
    attrs = _cand_attrs(candidate)
    sensitivity = context.get("sensitivity", context.get("context_sensitivity", {}))

    # -- Allergen conflict check --
    if sensitivity.get("allergen_sensitive"):
        context_allergens = {
            a.lower() for a in sensitivity.get("allergens_of_concern", [])
        }
        cand_allergens: set[str] = set()
        # From candidate attributes
        for a in attrs.get("allergens", []):
            cand_allergens.add(a.lower())
        # From evidence
        for e in evidence_items:
            ct = e.get("claim_type", e.get("type", ""))
            if ct in ("allergen_statement", "allergen"):
                declared = e.get("allergens", e.get("contains", []))
                if isinstance(declared, list):
                    cand_allergens.update(a.lower() for a in declared)
                elif isinstance(declared, str):
                    cand_allergens.add(declared.lower())

        overlap = context_allergens & cand_allergens
        if overlap:
            failures.append(
                f"Allergen conflict: candidate contains {', '.join(sorted(overlap))}"
            )

    # -- Banned substance check --
    banned = sensitivity.get("banned_substances", [])
    if banned:
        name_lower = name.lower()
        for substance in banned:
            if substance.lower() in name_lower:
                failures.append(f"Banned substance match: {substance}")

    # Check evidence for unresolved safety flags
    safety_claims = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("safety_flag", "safety", "hazard")
    ]
    unresolved = [
        e for e in safety_claims
        if not e.get("resolved", False)
    ]
    for e in unresolved:
        desc = e.get("description", e.get("claim", e.get("value", "unspecified safety concern")))
        failures.append(f"Unresolved safety flag: {desc}")

    if failures:
        return False, "; ".join(failures)
    return True, "No safety concerns identified"


# ---------------------------------------------------------------------------
# Dimension 1: Functional Fit
# ---------------------------------------------------------------------------

_CANDIDATE_TYPE_BASE = {
    "near_variant": 85.0,
    "same_family": 70.0,
    "normalization_variant": 75.0,
    "broader_functional": 55.0,
    "commercial_adjacency": 35.0,
}


def _score_functional_fit(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
) -> tuple[float, dict]:
    """
    Dimension 1 -- Functional Fit (0-100).

    Signals: functional_category match, BOM co-occurrence score,
    name similarity. Uses weights from candidate.signals if available.
    """
    name = _cand_name(candidate)
    ctype = _cand_type(candidate)
    signals = _cand_signals(candidate)
    details: dict[str, Any] = {}

    # -- Base score from candidate type --
    base = _CANDIDATE_TYPE_BASE.get(ctype, 50.0)
    details["type_base"] = base

    # -- Functional category match --
    target_name = ""
    target_ctx = context.get("target", {})
    if isinstance(target_ctx, dict):
        target_name = target_ctx.get("ingredient_name", target_ctx.get("name", ""))
    elif isinstance(target_ctx, str):
        target_name = target_ctx

    cat_score = 0.0
    if target_name:
        target_cat = _category_for(target_name)
        cand_cat = _category_for(name)
        if target_cat and cand_cat and target_cat == cand_cat:
            cat_score = 20.0
        elif target_cat and cand_cat:
            cat_score = 5.0  # different category but both categorised
    details["category_match_bonus"] = cat_score

    # -- BOM co-occurrence signal --
    bom_score = signals.get("bom_context", 0.0) * 15.0  # scale 0-1 -> 0-15
    details["bom_cooccurrence"] = round(bom_score, 2)

    # -- Name similarity signal --
    name_sim = signals.get("name_similarity", 0.0)
    if name_sim == 0.0 and target_name:
        name_sim = SequenceMatcher(None, target_name.lower(), name.lower()).ratio()
    name_score = name_sim * 10.0  # scale 0-1 -> 0-10
    details["name_similarity"] = round(name_score, 2)

    # -- Evidence bonus: verified technical claims --
    tech_claims = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("technical_property", "technical")
    ]
    verified_tech = [e for e in tech_claims if _is_verified(e)]
    evidence_bonus = min(len(verified_tech) * 3.0, 12.0)
    details["evidence_bonus"] = evidence_bonus

    # -- Combine --
    raw = base * 0.5 + cat_score + bom_score + name_score + evidence_bonus
    score = max(0.0, min(100.0, raw))
    details["raw"] = round(raw, 2)

    return round(score, 1), details


# ---------------------------------------------------------------------------
# Dimension 2: Compliance Fit
# ---------------------------------------------------------------------------

def _score_compliance_fit(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
) -> tuple[float, dict]:
    """
    Dimension 2 -- Compliance Fit (0-100).

    Uses score_compliance() from src.procurement.ranking.
    Also considers hard_constraints from sensitivity flags.
    """
    details: dict[str, Any] = {}

    # Build a pseudo-supplier dict for the ranking scorer
    compliance_ctx = context.get("compliance", {})
    sensitivity = context.get("sensitivity", context.get("context_sensitivity", {}))
    region = "eu"
    markets = (
        compliance_ctx.get("required_markets", [])
        or compliance_ctx.get("markets", [])
        or context.get("required_markets", [])
    )
    if markets:
        market_str = " ".join(markets).lower()
        if any(kw in market_str for kw in ("usa", "us", "united states", "america")):
            region = "usa"

    # Gather certifications from evidence
    certs: list[str] = []
    for e in evidence_items:
        ct = e.get("claim_type", e.get("type", ""))
        if ct in ("certification", "cert", "regulatory_status", "regulatory"):
            val = e.get("value", e.get("claim", ""))
            if val:
                certs.append(str(val))
            name_val = e.get("name", "")
            if name_val:
                certs.append(str(name_val))

    supplier_proxy = {
        "certifications": certs,
        "region": region,
        "snippet": " ".join(certs),
    }

    base_score = score_compliance(supplier_proxy, region=region)
    details["procurement_compliance_score"] = base_score

    # Verified evidence bonus
    reg_verified = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("regulatory_status", "regulatory")
        and _is_verified(e)
    ]
    cert_verified = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("certification", "cert")
        and _is_verified(e)
    ]
    verified_bonus = min((len(reg_verified) + len(cert_verified)) * 5.0, 20.0)
    details["verified_evidence_bonus"] = verified_bonus

    # Sensitivity penalty: hard constraints that are partially unmet
    sensitivity_penalty = 0.0
    if sensitivity.get("allergen_sensitive"):
        # Lack of allergen clearance reduces compliance confidence
        allergen_clear = any(
            e.get("claim_type", e.get("type", "")) in ("allergen_statement", "allergen")
            and _is_verified(e)
            for e in evidence_items
        )
        if not allergen_clear:
            sensitivity_penalty += 8.0
    if sensitivity.get("organic_certification_required"):
        has_organic = any(
            "organic" in str(e.get("value", "")).lower()
            for e in evidence_items
            if e.get("claim_type", e.get("type", "")) in ("certification", "cert")
            and _is_verified(e)
        )
        if not has_organic:
            sensitivity_penalty += 10.0
    details["sensitivity_penalty"] = sensitivity_penalty

    score = max(0.0, min(100.0, base_score + verified_bonus - sensitivity_penalty))
    return round(score, 1), details


# ---------------------------------------------------------------------------
# Dimension 3: Supply Viability
# ---------------------------------------------------------------------------

def _score_supply_viability(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
    cpg_db: Optional["CpgDatabase"] = None,
) -> tuple[float, dict]:
    """
    Dimension 3 -- Supply Viability (0-100).

    Supplier count, geographic diversity, single-source risk,
    supplier quality scores.
    """
    name = _cand_name(candidate)
    details: dict[str, Any] = {}

    # Gather suppliers
    supplier_names: set[str] = set()
    supplier_regions: set[str] = set()

    # From evidence
    linkage_claims = [
        e for e in evidence_items
        if e.get("claim_type", e.get("type", "")) in ("supplier_linkage", "supplier")
    ]
    for e in linkage_claims:
        s = e.get("supplier_name", e.get("supplier", ""))
        if s:
            supplier_names.add(s.lower())
        r = e.get("region", e.get("country", ""))
        if r:
            supplier_regions.add(r.lower())

    # From CpgDatabase
    db_suppliers: list[dict] = []
    if cpg_db and name:
        try:
            search_results = cpg_db.search_ingredients(name, limit=3)
            for sr in search_results:
                for pid in sr.get("product_ids", []):
                    suppliers = cpg_db.get_suppliers_for_product(pid)
                    for s in suppliers:
                        sn = s.get("Name", s.get("name", ""))
                        if sn:
                            supplier_names.add(sn.lower())
                            db_suppliers.append(s)
                        sr_region = s.get("Region", s.get("region", ""))
                        if sr_region:
                            supplier_regions.add(sr_region.lower())
        except Exception:
            pass

    n_suppliers = len(supplier_names)
    n_regions = len(supplier_regions)
    details["supplier_count"] = n_suppliers
    details["geographic_regions"] = n_regions

    # Supplier count scoring (0-40)
    if n_suppliers == 0:
        count_score = 0.0
    elif n_suppliers == 1:
        count_score = 15.0  # single source risk
    elif n_suppliers == 2:
        count_score = 25.0
    elif n_suppliers <= 4:
        count_score = 33.0
    else:
        count_score = 40.0
    details["count_score"] = count_score

    # Geographic diversity bonus (0-15)
    if n_regions >= 3:
        geo_bonus = 15.0
    elif n_regions == 2:
        geo_bonus = 10.0
    elif n_regions == 1:
        geo_bonus = 5.0
    else:
        geo_bonus = 0.0
    details["geo_diversity_bonus"] = geo_bonus

    # Single-source risk penalty
    single_source_penalty = 0.0
    if n_suppliers == 1:
        single_source_penalty = 12.0
    details["single_source_penalty"] = single_source_penalty

    # Supplier quality scores via score_quality (0-25)
    quality_scores: list[float] = []
    for s in db_suppliers:
        qs = score_quality(s)
        quality_scores.append(qs)
    if quality_scores:
        avg_quality = sum(quality_scores) / len(quality_scores)
        quality_component = (avg_quality / 100.0) * 25.0
    else:
        quality_component = 10.0  # neutral when unknown
    details["supplier_quality_component"] = round(quality_component, 1)

    # Price and lead-time signals from evidence
    price_signal = 0.0
    lt_signal = 0.0
    for e in evidence_items:
        ct = e.get("claim_type", e.get("type", ""))
        if ct in ("price", "pricing"):
            price_signal = 5.0
        if ct in ("lead_time", "delivery"):
            lt_signal = 5.0
    details["price_signal"] = price_signal
    details["lead_time_signal"] = lt_signal

    raw = count_score + geo_bonus - single_source_penalty + quality_component + price_signal + lt_signal
    score = max(0.0, min(100.0, raw))

    return round(score, 1), details


# ---------------------------------------------------------------------------
# Dimension 4: Operational Fit
# ---------------------------------------------------------------------------

def _score_operational_fit(
    candidate: Any,
    context: dict,
    evidence_items: list[dict],
    cpg_db: Optional["CpgDatabase"] = None,
) -> tuple[float, dict]:
    """
    Dimension 4 -- Operational Fit (0-100).

    Transition ease: variant proximity, BOM compatibility,
    how many BOMs already use this candidate.
    """
    name = _cand_name(candidate)
    ctype = _cand_type(candidate)
    signals = _cand_signals(candidate)
    product_ids = _cand_product_ids(candidate)
    details: dict[str, Any] = {}

    score = 50.0  # neutral baseline

    # -- Variant proximity: near_variants are close to drop-in --
    if ctype == "near_variant":
        score += 20.0
        details["variant_proximity_bonus"] = 20.0
    elif ctype == "same_family":
        score += 12.0
        details["variant_proximity_bonus"] = 12.0
    elif ctype == "normalization_variant":
        score += 15.0
        details["variant_proximity_bonus"] = 15.0
    elif ctype == "broader_functional":
        score += 0.0
        details["variant_proximity_bonus"] = 0.0
    elif ctype == "commercial_adjacency":
        score -= 10.0
        details["variant_proximity_bonus"] = -10.0
    else:
        details["variant_proximity_bonus"] = 0.0

    # -- BOM compatibility: how many BOMs already use this candidate --
    bom_count = 0
    if cpg_db and name:
        try:
            ing_to_boms = cpg_db.ingredient_to_boms()
            boms = ing_to_boms.get(name, ing_to_boms.get(name.lower(), set()))
            bom_count = len(boms)
        except Exception:
            pass
    if bom_count == 0:
        bom_count = len(product_ids)

    if bom_count >= 5:
        bom_bonus = 15.0
    elif bom_count >= 3:
        bom_bonus = 10.0
    elif bom_count >= 1:
        bom_bonus = 5.0
    else:
        bom_bonus = 0.0
    score += bom_bonus
    details["bom_usage_count"] = bom_count
    details["bom_compatibility_bonus"] = bom_bonus

    # -- Drop-in closeness from name similarity --
    name_sim = signals.get("name_similarity", 0.0)
    dropin_bonus = name_sim * 10.0
    score += dropin_bonus
    details["dropin_similarity_bonus"] = round(dropin_bonus, 2)

    # -- Functional adjacency signal --
    func_adj = signals.get("functional_adjacency", 0.0)
    func_bonus = func_adj * 8.0
    score += func_bonus
    details["functional_adjacency_bonus"] = round(func_bonus, 2)

    # -- Supplier adjacency signal (shared suppliers ease transition) --
    supplier_adj = signals.get("supplier_adjacency", 0.0)
    supplier_bonus = supplier_adj * 5.0
    score += supplier_bonus
    details["supplier_adjacency_bonus"] = round(supplier_bonus, 2)

    score = max(0.0, min(100.0, score))
    return round(score, 1), details


# ---------------------------------------------------------------------------
# FeasibilityScorer
# ---------------------------------------------------------------------------

class FeasibilityScorer:
    """
    Scores substitution candidates across 4 dimensions after applying
    4 hard gates (P2 Fix 3).

    Parameters
    ----------
    cpg_db : CpgDatabase
        The CPG database instance for supplier/BOM lookups.
    """

    def __init__(
        self,
        cpg_db: Optional["CpgDatabase"] = None,
        weights: dict[str, float] | None = None,
    ):
        self.db = cpg_db
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        # Normalise weights to sum to 1.0
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    def score(
        self,
        candidate: Any,
        context: dict,
        evidence: Any = None,
    ) -> ScoreCard:
        """
        Score a single candidate through hard gates and 4 dimensions.

        Parameters
        ----------
        candidate : Candidate dataclass or dict
            Must have: canonical_name, candidate_type, signals dict,
            product_ids, validation_status.
        context : dict
            From AgnesContext.build() with keys: target, product, demand,
            supply, formulation, variant, compliance, sensitivity.
        evidence : list[dict] or dict
            Evidence items with keys: source, source_tier, claim_type,
            verified (bool), etc.

        Returns
        -------
        ScoreCard
        """
        name = _cand_name(candidate)
        evidence_items = _evidence_list(evidence)

        # ── Hard gates ────────────────────────────────────────────────
        gate_failures: list[str] = []

        g1_passed, g1_reason = _gate_compliance_floor(candidate, context, evidence_items)
        if not g1_passed:
            gate_failures.append(f"Compliance floor: {g1_reason}")

        g2_passed, g2_reason = _gate_functional_floor(candidate, evidence_items)
        if not g2_passed:
            gate_failures.append(f"Functional floor: {g2_reason}")

        g3_passed, g3_reason = _gate_supply_availability(
            candidate, context, evidence_items, self.db
        )
        if not g3_passed:
            gate_failures.append(f"Supply availability: {g3_reason}")

        g4_passed, g4_reason = _gate_safety(candidate, context, evidence_items)
        if not g4_passed:
            gate_failures.append(f"Safety: {g4_reason}")

        gate_status = "passed" if not gate_failures else "blocked"

        # ── Dimension scores ──────────────────────────────────────────
        func_score, func_details = _score_functional_fit(candidate, context, evidence_items)
        comp_score, comp_details = _score_compliance_fit(candidate, context, evidence_items)
        supply_score, supply_details = _score_supply_viability(
            candidate, context, evidence_items, self.db
        )
        ops_score, ops_details = _score_operational_fit(
            candidate, context, evidence_items, self.db
        )

        # ── Composite (weighted average) ──────────────────────────────
        composite = (
            func_score * self.weights.get("functional_fit", 0.30)
            + comp_score * self.weights.get("compliance_fit", 0.30)
            + supply_score * self.weights.get("supply_viability", 0.20)
            + ops_score * self.weights.get("operational_fit", 0.20)
        )

        dimension_details = {
            "functional_fit": func_details,
            "compliance_fit": comp_details,
            "supply_viability": supply_details,
            "operational_fit": ops_details,
            "gates": {
                "compliance_floor": {"passed": g1_passed, "reason": g1_reason},
                "functional_floor": {"passed": g2_passed, "reason": g2_reason},
                "supply_availability": {"passed": g3_passed, "reason": g3_reason},
                "safety": {"passed": g4_passed, "reason": g4_reason},
            },
        }

        return ScoreCard(
            candidate_name=name,
            gate_status=gate_status,
            gate_failures=gate_failures,
            functional_fit=func_score,
            compliance_fit=comp_score,
            supply_viability=supply_score,
            operational_fit=ops_score,
            composite=round(composite, 1),
            dimension_details=dimension_details,
        )

    def score_all(
        self,
        candidates: list,
        context: dict,
        evidence_map: Any = None,
    ) -> list[ScoreCard]:
        """
        Score all candidates, return sorted by composite (blocked at bottom).

        Parameters
        ----------
        candidates : list of Candidate dataclass or dict
        context : dict from AgnesContext.build()
        evidence_map : dict keyed by candidate name -> evidence list,
                       or a single evidence list shared by all candidates.

        Returns
        -------
        list[ScoreCard] sorted by composite descending, blocked at bottom.
        """
        cards: list[ScoreCard] = []

        for cand in candidates:
            name = _cand_name(cand)

            # Resolve per-candidate evidence
            cand_evidence: Any = None
            if isinstance(evidence_map, dict):
                # Try exact name lookup, then lowercase
                cand_evidence = evidence_map.get(
                    name,
                    evidence_map.get(name.lower(), evidence_map.get("_shared", None)),
                )
                # If evidence_map has a "claims" key it is a single shared bundle
                if cand_evidence is None and "claims" in evidence_map:
                    cand_evidence = evidence_map
            elif isinstance(evidence_map, list):
                cand_evidence = evidence_map
            else:
                cand_evidence = evidence_map

            card = self.score(cand, context, cand_evidence)
            cards.append(card)

        # Sort: passed first (by composite desc), then blocked (by composite desc)
        def _sort_key(c: ScoreCard) -> tuple:
            status_order = 0 if c.gate_status == "passed" else 1
            return (status_order, -c.composite)

        cards.sort(key=_sort_key)
        return cards
