"""
Evidence Collector — Step 4 of the Agnes CPG pipeline.

Gathers evidence for each substitution candidate from multiple sources,
assigns trust tiers, and handles conflicting claims.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.procurement.substitution_engine import FUNCTIONAL_CATEGORIES

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase

# ── Trust tiers ───────────────────────────────────────────────────────

TRUST_TIERS: dict[str, dict[str, Any]] = {
    "T1": {"source_types": {"regulatory_database", "certification_body"}, "weight": 1.0},
    "T2": {"source_types": {"supplier_spec", "supplier_coa"}, "weight": 0.8},
    "T3": {"source_types": {"published_study", "industry_database"}, "weight": 0.7},
    "T4": {"source_types": {"marketplace_listing"}, "weight": 0.5},
    "T5": {"source_types": {"inferred"}, "weight": 0.3},
}

# Reverse lookup: source_type -> tier key
_SOURCE_TO_TIER: dict[str, str] = {}
for _tier_key, _tier_info in TRUST_TIERS.items():
    for _st in _tier_info["source_types"]:
        _SOURCE_TO_TIER[_st] = _tier_key


def _tier_for_source(source_type: str) -> tuple[str, float]:
    """Return (tier_key, trust_weight) for a given source type."""
    tier_key = _SOURCE_TO_TIER.get(source_type, "T5")
    return tier_key, TRUST_TIERS[tier_key]["weight"]


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class EvidenceRecord:
    candidate_name: str
    source: str  # e.g. "cpg_database", "alibaba_scraper", "bom_inference"
    source_tier: str  # "T1" through "T5"
    trust_weight: float
    claim_type: str  # "regulatory_status", "technical_property", "price_point", "certification", "functional_claim"
    claim: str  # human-readable
    verified: bool  # T1/T2 are auto-verified, others need cross-reference
    conflicts_with: list[str] = field(default_factory=list)  # IDs of conflicting evidence records
    evidence_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


# ── Helpers ───────────────────────────────────────────────────────────

_NEGATION_PAIRS: list[tuple[str, str]] = [
    ("approved", "not approved"),
    ("gras", "not gras"),
    ("compliant", "non-compliant"),
    ("certified", "not certified"),
    ("kosher", "not kosher"),
    ("halal", "not halal"),
    ("organic", "not organic"),
    ("vegan", "not vegan"),
    ("permitted", "not permitted"),
]


def _claims_conflict(a: EvidenceRecord, b: EvidenceRecord) -> bool:
    """Detect whether two evidence records carry contradictory claims."""
    if a.claim_type != b.claim_type:
        return False
    if a.candidate_name != b.candidate_name:
        return False

    ca = a.claim.lower()
    cb = b.claim.lower()

    # Exact duplicates are not conflicts
    if ca == cb:
        return False

    # Check known negation pairs
    for pos, neg in _NEGATION_PAIRS:
        a_pos = pos in ca and neg not in ca
        a_neg = neg in ca
        b_pos = pos in cb and neg not in cb
        b_neg = neg in cb
        if (a_pos and b_neg) or (a_neg and b_pos):
            return True

    return False


# ── Collector ─────────────────────────────────────────────────────────


class EvidenceCollector:
    """Collect, tier, and reconcile evidence for substitution candidates."""

    def __init__(self, cpg_db: CpgDatabase):
        self.cpg_db = cpg_db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self, candidate: dict, context: dict | None = None) -> list[EvidenceRecord]:
        """Collect evidence for a single candidate.

        Parameters
        ----------
        candidate : dict
            Must contain at least ``ingredient_name`` (str).  May also include
            ``product_ids`` (list[int]) for database cross-referencing.
        context : dict, optional
            Pipeline context (e.g. ``{"region": "USA"}``) passed from earlier
            steps.
        """
        context = context or {}
        records: list[EvidenceRecord] = []

        name: str = candidate.get("ingredient_name", candidate.get("name", ""))
        product_ids: list[int] = candidate.get("product_ids", [])

        records.extend(self._from_cpg_database(name, product_ids))
        records.extend(self._from_bom_patterns(name))
        records.extend(self._from_supplier_graph(name, product_ids))
        records.extend(self._from_functional_category(name))

        return records

    def collect_all(
        self, candidates: list[dict], context: dict | None = None
    ) -> dict[str, list[EvidenceRecord]]:
        """Collect evidence for every candidate.

        Returns a mapping of ``candidate_name -> [EvidenceRecord, ...]``.
        """
        result: dict[str, list[EvidenceRecord]] = {}
        for cand in candidates:
            name = cand.get("ingredient_name", cand.get("name", ""))
            records = self.collect(cand, context)
            records = self.resolve_conflicts(records)
            result[name] = records
        return result

    def resolve_conflicts(self, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
        """Detect and mark conflicting evidence records.

        Both sides of a conflict are preserved; the ``conflicts_with`` field
        on each record references the other's ``evidence_id``.  The higher-tier
        record keeps ``verified=True`` (if applicable); the lower-tier record
        is marked ``verified=False``.
        """
        n = len(records)
        for i in range(n):
            for j in range(i + 1, n):
                if _claims_conflict(records[i], records[j]):
                    if records[j].evidence_id not in records[i].conflicts_with:
                        records[i].conflicts_with.append(records[j].evidence_id)
                    if records[i].evidence_id not in records[j].conflicts_with:
                        records[j].conflicts_with.append(records[i].evidence_id)

                    # Higher tier wins — demote the weaker record
                    if records[i].trust_weight >= records[j].trust_weight:
                        records[j].verified = False
                    else:
                        records[i].verified = False

        return records

    def quality_score(self, records: list[EvidenceRecord]) -> float:
        """Compute an aggregate evidence quality score in [0.0, 1.0].

        Scoring formula:
        - Each record contributes its ``trust_weight`` (adjusted down if
          unverified or conflicted).
        - The raw sum is normalised against a theoretical maximum of five
          T1-level records (weight 1.0 each).
        """
        if not records:
            return 0.0

        MAX_IDEAL = 5.0  # five perfect T1 records

        total = 0.0
        for r in records:
            w = r.trust_weight
            if not r.verified:
                w *= 0.5
            if r.conflicts_with:
                w *= 0.7
            total += w

        return min(total / MAX_IDEAL, 1.0)

    # ------------------------------------------------------------------
    # Evidence sources (private)
    # ------------------------------------------------------------------

    def _from_cpg_database(self, name: str, product_ids: list[int]) -> list[EvidenceRecord]:
        """Query Supplier_Product links and supplier certifications (T1/T2)."""
        records: list[EvidenceRecord] = []

        for pid in product_ids:
            suppliers = self.cpg_db.get_suppliers_for_product(pid)
            for sup in suppliers:
                tier, weight = _tier_for_source("supplier_spec")
                records.append(
                    EvidenceRecord(
                        candidate_name=name,
                        source="cpg_database",
                        source_tier=tier,
                        trust_weight=weight,
                        claim_type="certification",
                        claim=f"Supplied by {sup['Name']} (supplier ID {sup['Id']})",
                        verified=True,  # T2 auto-verified
                    )
                )

        # If the ingredient exists in the database at all, that is T2 evidence
        search_results = self.cpg_db.search_ingredients(name, limit=1)
        if search_results:
            tier, weight = _tier_for_source("supplier_spec")
            records.append(
                EvidenceRecord(
                    candidate_name=name,
                    source="cpg_database",
                    source_tier=tier,
                    trust_weight=weight,
                    claim_type="regulatory_status",
                    claim=f"{name} is present in the CPG ingredient database",
                    verified=True,
                )
            )

        return records

    def _from_bom_patterns(self, name: str) -> list[EvidenceRecord]:
        """Infer claims from BOM co-occurrence data (T5)."""
        records: list[EvidenceRecord] = []

        ingredient_boms = self.cpg_db.ingredient_to_boms()
        bom_ids = ingredient_boms.get(name, set())
        if not bom_ids:
            # Try case-insensitive lookup
            for key, ids in ingredient_boms.items():
                if key.lower() == name.lower():
                    bom_ids = ids
                    break

        if bom_ids:
            count = len(bom_ids)
            tier, weight = _tier_for_source("inferred")

            records.append(
                EvidenceRecord(
                    candidate_name=name,
                    source="bom_inference",
                    source_tier=tier,
                    trust_weight=weight,
                    claim_type="functional_claim",
                    claim=f"Used in {count} BOM(s) — likely compatible with similar formulations",
                    verified=False,
                )
            )

            # Check co-occurring ingredients for category inference
            bom_sets = self.cpg_db.bom_ingredient_sets()
            cooccurrence: dict[str, int] = defaultdict(int)
            for bid in bom_ids:
                for ing in bom_sets.get(bid, set()):
                    if ing.lower() != name.lower():
                        cooccurrence[ing] += 1

            # Top 3 co-occurring ingredients as evidence
            top_cooccur = sorted(cooccurrence.items(), key=lambda x: x[1], reverse=True)[:3]
            if top_cooccur:
                partners = ", ".join(f"{ing} ({n}x)" for ing, n in top_cooccur)
                records.append(
                    EvidenceRecord(
                        candidate_name=name,
                        source="bom_inference",
                        source_tier=tier,
                        trust_weight=weight,
                        claim_type="technical_property",
                        claim=f"Commonly paired with: {partners}",
                        verified=False,
                    )
                )

        return records

    def _from_supplier_graph(self, name: str, product_ids: list[int]) -> list[EvidenceRecord]:
        """Which suppliers carry this ingredient and their breadth (T4/T5)."""
        records: list[EvidenceRecord] = []

        catalog = self.cpg_db.get_supplier_catalog()

        carrying_suppliers: list[str] = []
        for supplier_name, ingredients in catalog.items():
            normed = [i.lower() for i in ingredients]
            if name.lower() in normed:
                carrying_suppliers.append(supplier_name)

        if carrying_suppliers:
            tier, weight = _tier_for_source("marketplace_listing")
            records.append(
                EvidenceRecord(
                    candidate_name=name,
                    source="supplier_graph",
                    source_tier=tier,
                    trust_weight=weight,
                    claim_type="certification",
                    claim=(
                        f"Available from {len(carrying_suppliers)} supplier(s): "
                        + ", ".join(carrying_suppliers[:5])
                    ),
                    verified=False,
                )
            )

            # Multi-supplier availability is a weak positive signal
            if len(carrying_suppliers) >= 3:
                tier_inf, weight_inf = _tier_for_source("inferred")
                records.append(
                    EvidenceRecord(
                        candidate_name=name,
                        source="supplier_graph",
                        source_tier=tier_inf,
                        trust_weight=weight_inf,
                        claim_type="functional_claim",
                        claim=(
                            f"Widely available ({len(carrying_suppliers)} suppliers) "
                            "— lower supply-chain risk"
                        ),
                        verified=False,
                    )
                )

        return records

    def _from_functional_category(self, name: str) -> list[EvidenceRecord]:
        """Check membership in FUNCTIONAL_CATEGORIES (T5)."""
        records: list[EvidenceRecord] = []
        tier, weight = _tier_for_source("inferred")

        name_lower = name.lower().strip()
        for category, members in FUNCTIONAL_CATEGORIES.items():
            normed_members = [m.lower() for m in members]
            if name_lower in normed_members or any(
                name_lower in m or m in name_lower for m in normed_members
            ):
                records.append(
                    EvidenceRecord(
                        candidate_name=name,
                        source="functional_category",
                        source_tier=tier,
                        trust_weight=weight,
                        claim_type="functional_claim",
                        claim=f"Classified as '{category}' in functional categories",
                        verified=False,
                    )
                )
                break  # one category match is sufficient

        return records
