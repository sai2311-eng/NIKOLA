"""
Procurement Ranking Engine — Stage 5 of the Procurement Pipeline.

Four primary signals (user-configurable weights):
  quality        — certifications, quality rating, data confidence
  compliance     — region-aware (EU: REACH/RoHS or USA: FDA/GRAS/USP)
  price          — absolute or relative to an internal benchmark
  lead_time      — shorter is always better

All signals return 0-100.  Composite = weighted sum.

Verdicts
--------
  excellent  >= 78
  good       >= 62
  possible   >= 46
  limited    >= 30
  poor        < 30
"""

from __future__ import annotations

import re
from typing import Optional

# ── keyword lists ─────────────────────────────────────────────────────────────

_EU_KEYWORDS = [
    "ce", "ce marking", "reach", "rohs", "rohs2",
    "en 10204", "en 10025", "din", "vde", "tuv", "tüv", "dekra", "bsi",
    "iso 9001", "iso 14001", "iatf 16949", "as9100", "nadcap",
    "eumos", "echa", "weee",
]

_QUALITY_KEYWORDS = [
    "iso 9001", "iso 14001", "iatf 16949", "as9100", "nadcap",
    "api", "pma", "a2la", "iso/iec 17025", "ts 16949", "ppap",
    "mil-spec", "mil spec", "aerospace", "certified",
]

_EU_REGIONS = {
    "germany", "france", "italy", "spain", "netherlands", "belgium",
    "sweden", "austria", "switzerland", "poland", "uk", "europe", "eu",
    "czech", "denmark", "finland", "portugal", "slovakia",
}

# ── USA compliance keywords ──────────────────────────────────────────────────

_USA_KEYWORDS = [
    "fda", "fda registered", "21 cfr", "gras", "generally recognized as safe",
    "ftc", "usp", "nf", "nsf", "nsf international",
    "gmp", "cgmp", "current good manufacturing practice",
    "dietary supplement", "dshea", "otc monograph",
    "prop 65", "california prop 65", "usda organic",
    "non-gmo", "non gmo project", "kosher", "halal",
    "usp verified", "nsf certified for sport",
    "informed sport", "banned substances tested",
]

_USA_REGIONS = {
    "usa", "us", "united states", "america", "canada",
    "mexico", "north america",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = re.sub(r"[^\d.]", "", str(val))
        return float(cleaned) if cleaned else None
    except Exception:
        return None


def _cert_text(supplier: dict) -> str:
    certs = supplier.get("certifications", [])
    if isinstance(certs, str):
        certs = [c.strip() for c in certs.split(",")]
    return (" ".join(str(c) for c in certs)
            + " " + supplier.get("snippet", "")).lower()


# ── individual signal scorers ─────────────────────────────────────────────────

def score_quality(supplier: dict) -> float:
    score = 0.0

    # Explicit quality rating (0–5 scale → up to 60 pts)
    qr = _to_float(supplier.get("quality_rating"))
    if qr is not None:
        # Known rating: score linearly and apply confidence modifier
        base = min(qr / 5.0, 1.0) * 60.0
        conf = float(supplier.get("confidence", 0.5))
        score += base * (0.90 + 0.10 * conf)
    else:
        # No rating available — true neutral (50), no penalty for missing data
        score += 50.0

    # Certifications (up to 35 pts, 5 pts each)
    ct = _cert_text(supplier)
    cert_pts = sum(5 for kw in _QUALITY_KEYWORDS if kw in ct)
    score += min(cert_pts, 35.0)

    return min(round(score, 1), 100.0)


def score_eu_compliance(supplier: dict) -> float:
    ct = _cert_text(supplier)
    region = supplier.get("region", "").lower()

    matches = sum(1 for kw in _EU_KEYWORDS if kw in ct)

    if matches == 0:
        score = 45.0  # unknown — neutral (not penalised)
    else:
        score = min(40.0 + matches * 8.0, 90.0)

    # European-region bonus
    if any(r in region for r in _EU_REGIONS):
        score = min(score + 15.0, 100.0)

    return min(round(score, 1), 100.0)


def score_usa_compliance(supplier: dict) -> float:
    """Score supplier on USA regulatory signals (FDA, GRAS, USP, GMP, etc.)."""
    ct = _cert_text(supplier)
    region = supplier.get("region", "").lower()

    matches = sum(1 for kw in _USA_KEYWORDS if kw in ct)

    if matches == 0:
        score = 45.0  # unknown -- neutral
    else:
        score = min(40.0 + matches * 7.0, 90.0)

    # US-region bonus
    if any(r in region for r in _USA_REGIONS):
        score = min(score + 15.0, 100.0)

    return min(round(score, 1), 100.0)


def score_compliance(supplier: dict, region: str = "eu") -> float:
    """Dispatch to region-specific compliance scorer."""
    if region.lower() in ("usa", "us", "united states"):
        return score_usa_compliance(supplier)
    return score_eu_compliance(supplier)


def score_price(
    supplier: dict,
    reference_price: Optional[float] = None,
) -> float:
    price = _to_float(supplier.get("price_usd"))

    if price is None:
        return 50.0  # unknown price — neutral

    if reference_price and reference_price > 0:
        ratio = price / reference_price
        if ratio <= 0.80:   return 100.0
        if ratio <= 0.90:   return 92.0
        if ratio <= 1.00:   return 85.0
        if ratio <= 1.10:   return 75.0
        if ratio <= 1.25:   return 62.0
        if ratio <= 1.50:   return 48.0
        if ratio <= 2.00:   return 30.0
        return 15.0
    else:
        # Absolute scoring (rough commodity tiers)
        if price < 1:       return 88.0
        if price < 10:      return 74.0
        if price < 100:     return 60.0
        if price < 1_000:   return 45.0
        return 30.0


def score_lead_time(
    supplier: dict,
    reference_days: Optional[int] = None,
) -> float:
    lt_raw = supplier.get("lead_time_days")
    if lt_raw is None:
        return 50.0  # unknown — neutral

    lt = _to_float(lt_raw)
    if lt is None:
        return 50.0

    if reference_days and reference_days > 0:
        ratio = lt / reference_days
        if ratio <= 0.50:   return 100.0
        if ratio <= 0.75:   return 90.0
        if ratio <= 1.00:   return 80.0
        if ratio <= 1.50:   return 62.0
        if ratio <= 2.00:   return 44.0
        return 20.0
    else:
        if lt <= 3:    return 100.0
        if lt <= 7:    return 92.0
        if lt <= 14:   return 82.0
        if lt <= 21:   return 72.0
        if lt <= 30:   return 60.0
        if lt <= 60:   return 42.0
        return 22.0


# ── ranker class ──────────────────────────────────────────────────────────────

class ProcurementRanker:
    """
    Multi-signal ranking engine for procurement supplier lists.

    Default weights  (all 4 signals active):
      quality        35 %
      eu_compliance  25 %
      price          25 %
      lead_time      15 %
    """

    DEFAULT_WEIGHTS = {
        "quality":    0.35,
        "compliance": 0.25,
        "price":      0.25,
        "lead_time":  0.15,
    }

    def rank(
        self,
        suppliers: list[dict],
        material: dict = None,
        weights: dict = None,
        reference_price: Optional[float] = None,
        reference_lead_time_days: Optional[int] = None,
        top_n: Optional[int] = None,
        active_signals: Optional[list[str]] = None,
        compliance_region: str = "usa",
    ) -> list[dict]:
        """
        Score and sort suppliers.

        Parameters
        ----------
        suppliers               : raw supplier dicts from supply_intelligence
        material                : product identification result (for context)
        weights                 : override default signal weights
        reference_price         : internal benchmark price (USD/unit)
        reference_lead_time_days: internal benchmark lead time
        top_n                   : return only top N
        active_signals          : subset of signals to use
        compliance_region       : "eu" or "usa"

        Returns
        -------
        Sorted list; each supplier gains:
          scores, composite_score, verdict, rank
        """
        if not suppliers:
            return []

        # Build effective weight map — accept both old "eu_compliance" and new "compliance" keys
        w = dict(self.DEFAULT_WEIGHTS)
        user_w = weights or {}
        # Migrate legacy key
        if "eu_compliance" in user_w and "compliance" not in user_w:
            user_w["compliance"] = user_w.pop("eu_compliance")
        w.update(user_w)
        # Normalise away legacy key internally
        if "eu_compliance" in w:
            w.setdefault("compliance", w.pop("eu_compliance"))

        if active_signals:
            # Also accept legacy name
            sigs = set(active_signals)
            if "eu_compliance" in sigs:
                sigs.discard("eu_compliance")
                sigs.add("compliance")
            w = {k: (v if k in sigs else 0.0) for k, v in w.items()}
        total_w = sum(w.values())
        if total_w > 0:
            w = {k: v / total_w for k, v in w.items()}

        compliance_label = "usa_compliance" if compliance_region.lower() in ("usa", "us") else "eu_compliance"

        scored: list[dict] = []
        for s in suppliers:
            q = score_quality(s)
            c = score_compliance(s, region=compliance_region)
            p = score_price(s, reference_price)
            lt = score_lead_time(s, reference_lead_time_days)

            composite = (
                q  * w.get("quality", 0)
                + c  * w.get("compliance", 0)
                + p  * w.get("price", 0)
                + lt * w.get("lead_time", 0)
            )

            scored.append({
                **s,
                "scores": {
                    "quality":         round(q, 1),
                    compliance_label:  round(c, 1),
                    "price":           round(p, 1),
                    "lead_time":       round(lt, 1),
                    "composite":       round(composite, 1),
                },
                "composite_score": round(composite, 1),
                "verdict":          self._verdict(composite),
            })

        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        if top_n:
            scored = scored[:top_n]

        for i, s in enumerate(scored, start=1):
            s["rank"] = i

        return scored

    @staticmethod
    def _verdict(score: float) -> str:
        if score >= 78:  return "excellent"
        if score >= 62:  return "good"
        if score >= 46:  return "possible"
        if score >= 30:  return "limited"
        return "poor"
