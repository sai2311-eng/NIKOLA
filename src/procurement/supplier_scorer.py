"""
Supplier Scoring & Ranking Engine

Scores suppliers on a 100-point scale across 5 dimensions:
  - Price competitiveness  (20 pts)
  - Quantity / MOQ fit     (15 pts)
  - Scalability            (20 pts)
  - Reliability            (25 pts)
  - Data completeness      (10 pts)
  + Triangulation bonus    (10 pts)

Tier assignment:
  Tier 1 - Primary     : score >= 70 AND triangulation_complete
  Tier 2 - Backup      : score >= 50 AND at least 2 triangulation checks
  Tier 3 - Conditional : score >= 30 OR incomplete data
  Tier 4 - Reject      : score < 30 OR any critical red flag
"""

from __future__ import annotations
from typing import Optional


# ── Weight configuration ─────────────────────────────────────────────────────

WEIGHTS = {
    "price":            20,
    "quantity":         15,
    "scalability":      20,
    "reliability":      25,
    "data_completeness": 10,
    "triangulation":    10,
}
# Total = 100

CERT_POINTS = {
    "cert_iso": 2.0,
    "cert_haccp": 1.5,
    "cert_reach": 2.0,
    "cert_ce_mark": 1.5,
    "cert_cpsc": 1.5,
    "cert_astm": 1.0,
    "cert_brc": 1.0,
    "cert_fssai": 0.5,
    "cert_bis": 0.5,
}
# Max cert contribution = 11.5, normalized to fit inside reliability weight


def score_supplier(supplier: dict) -> dict:
    """
    Score a single supplier dict. Returns the supplier dict with added fields:
      price_score, quantity_score, scalability_weighted, reliability_weighted,
      data_weighted, triangulation_bonus, final_score, tier_output, action,
      score_breakdown
    """
    breakdown = {}

    # ── Price score (0-20) ───────────────────────────────────────────────
    raw_price = _to_float(supplier.get("price_score"))
    if raw_price is None:
        raw_price = _estimate_price_score(supplier)
    price_weighted = (raw_price / 10) * WEIGHTS["price"]
    breakdown["price"] = round(price_weighted, 1)

    # ── Quantity / MOQ score (0-15) ──────────────────────────────────────
    raw_qty = _to_float(supplier.get("quantity_score"))
    if raw_qty is None:
        raw_qty = _estimate_quantity_score(supplier)
    qty_weighted = (raw_qty / 10) * WEIGHTS["quantity"]
    breakdown["quantity"] = round(qty_weighted, 1)

    # ── Scalability (0-20) ──────────────────────────────────────────────
    raw_scale = _to_float(supplier.get("scalability_score")) or 0
    scale_weighted = (raw_scale / 10) * WEIGHTS["scalability"]
    breakdown["scalability"] = round(scale_weighted, 1)

    # ── Reliability (0-25) ──────────────────────────────────────────────
    raw_rely = _to_float(supplier.get("reliability_score")) or 0
    # Boost reliability with cert count
    cert_bonus = _cert_score(supplier)  # 0-11.5 normalized to 0-5
    cert_norm = min(cert_bonus / 11.5 * 5, 5)
    rely_base = (raw_rely / 10) * (WEIGHTS["reliability"] - 5)  # 20 pts from raw
    rely_weighted = rely_base + cert_norm
    breakdown["reliability"] = round(rely_weighted, 1)
    breakdown["cert_bonus"] = round(cert_norm, 1)

    # ── Data completeness (0-10) ────────────────────────────────────────
    raw_data = _to_float(supplier.get("data_completeness_score")) or 0
    data_weighted = (raw_data / 10) * WEIGHTS["data_completeness"]
    breakdown["data_completeness"] = round(data_weighted, 1)

    # ── Triangulation bonus (0-10) ──────────────────────────────────────
    tri_reg = bool(supplier.get("triangulation_regulatory"))
    tri_fp = bool(supplier.get("triangulation_firstparty"))
    tri_trade = bool(supplier.get("triangulation_trade"))
    tri_count = sum([tri_reg, tri_fp, tri_trade])
    tri_complete = bool(supplier.get("triangulation_complete")) or tri_count == 3

    if tri_complete:
        tri_bonus = WEIGHTS["triangulation"]  # full 10
    else:
        tri_bonus = (tri_count / 3) * WEIGHTS["triangulation"]
    breakdown["triangulation"] = round(tri_bonus, 1)

    # ── Red flag penalties ──────────────────────────────────────────────
    penalty = 0
    penalties = []
    if supplier.get("recall_history"):
        penalty += 15
        penalties.append("Recall history (-15)")
    if supplier.get("eu_safety_gate_flagged"):
        penalty += 10
        penalties.append("EU Safety Gate flagged (-10)")
    if supplier.get("cpsc_recall_flagged"):
        penalty += 10
        penalties.append("CPSC recall flagged (-10)")
    if supplier.get("self_declared_only"):
        penalty += 5
        penalties.append("Self-declared certs only (-5)")

    cert_expiry = supplier.get("cert_expiry", "")
    if cert_expiry and _is_expired(cert_expiry):
        penalty += 8
        penalties.append("Expired certification (-8)")

    breakdown["penalties"] = penalties
    breakdown["total_penalty"] = penalty

    # ── Final score ─────────────────────────────────────────────────────
    raw_total = (
        price_weighted + qty_weighted + scale_weighted +
        rely_weighted + data_weighted + tri_bonus
    )
    final_score = max(0, min(100, raw_total - penalty))
    final_score = round(final_score, 1)

    # ── Tier assignment ─────────────────────────────────────────────────
    has_critical_flag = bool(
        supplier.get("recall_history") or
        supplier.get("eu_safety_gate_flagged") or
        supplier.get("cpsc_recall_flagged")
    )

    if has_critical_flag:
        tier = "Tier 4 - Reject"
        action = "Do not proceed — critical compliance failure"
    elif final_score >= 70 and tri_complete:
        tier = "Tier 1 - Primary"
        action = "Initiate RFQ — ready for negotiation"
    elif final_score >= 70 and not tri_complete:
        tier = "Tier 2 - Backup"
        action = f"Complete triangulation ({3 - tri_count} check(s) remaining)"
    elif final_score >= 50 and tri_count >= 2:
        tier = "Tier 2 - Backup"
        action = "Strong candidate — verify remaining gaps"
    elif final_score >= 50:
        tier = "Tier 3 - Conditional"
        action = "Needs more data and triangulation"
    elif final_score >= 30:
        tier = "Tier 3 - Conditional"
        action = "Incomplete — gather more evidence before deciding"
    else:
        tier = "Tier 4 - Reject"
        action = "Insufficient data or poor scores — not recommended"

    # ── Build result ────────────────────────────────────────────────────
    result = dict(supplier)
    result["final_score"] = final_score
    result["tier_output"] = tier
    result["action"] = action
    result["score_breakdown"] = breakdown
    return result


def rank_suppliers(suppliers: list[dict]) -> list[dict]:
    """Score and rank a list of suppliers. Returns sorted list (best first)."""
    scored = [score_supplier(s) for s in suppliers]
    scored.sort(key=lambda s: s["final_score"], reverse=True)
    for i, s in enumerate(scored):
        s["rank"] = i + 1
    return scored


def tier_summary(ranked: list[dict]) -> dict:
    """Generate a tier summary from ranked suppliers."""
    tiers = {
        "Tier 1 - Primary": [],
        "Tier 2 - Backup": [],
        "Tier 3 - Conditional": [],
        "Tier 4 - Reject": [],
    }
    for s in ranked:
        t = s.get("tier_output", "Tier 3 - Conditional")
        if t in tiers:
            tiers[t].append(s)
        else:
            tiers["Tier 3 - Conditional"].append(s)

    return {
        "total": len(ranked),
        "tier_1_count": len(tiers["Tier 1 - Primary"]),
        "tier_2_count": len(tiers["Tier 2 - Backup"]),
        "tier_3_count": len(tiers["Tier 3 - Conditional"]),
        "tier_4_count": len(tiers["Tier 4 - Reject"]),
        "tiers": tiers,
        "avg_score": round(
            sum(s["final_score"] for s in ranked) / len(ranked), 1
        ) if ranked else 0,
        "triangulated_count": sum(
            1 for s in ranked if s.get("triangulation_complete")
        ),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_float(val) -> Optional[float]:
    if val is None or val == "" or val == "null":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _estimate_price_score(supplier: dict) -> float:
    """Estimate price score from price_per_unit if raw score not given."""
    price = _to_float(supplier.get("price_per_unit"))
    if price is None:
        return 0
    # Without market context, give a moderate score if price exists
    return 5.0


def _estimate_quantity_score(supplier: dict) -> float:
    """Estimate quantity score from MOQ and capacity."""
    moq = _to_float(supplier.get("moq"))
    capacity = _to_float(supplier.get("monthly_capacity"))
    if moq is None and capacity is None:
        return 0
    score = 0
    if moq is not None:
        score += 3  # MOQ is known
    if capacity is not None:
        score += 3  # Capacity is known
        if capacity > 100000:
            score += 2  # High capacity
        elif capacity > 10000:
            score += 1
    if moq is not None and moq < 1000:
        score += 2  # Low MOQ = flexible
    return min(score, 10)


def _cert_score(supplier: dict) -> float:
    """Sum certification points."""
    total = 0
    for cert_field, points in CERT_POINTS.items():
        if supplier.get(cert_field):
            total += points
    # Bonus for verified certs
    if supplier.get("cert_verified_via"):
        total += 1.0
    return total


def _is_expired(date_str: str) -> bool:
    """Check if a date string (YYYY-MM-DD or YYYY-MM) is in the past."""
    from datetime import datetime
    try:
        if len(date_str) == 7:  # YYYY-MM
            dt = datetime.strptime(date_str, "%Y-%m")
        elif len(date_str) >= 10:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        else:
            return False
        return dt < datetime.now()
    except (ValueError, TypeError):
        return False
