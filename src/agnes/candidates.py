"""
Step 2 of the Agnes CPG pipeline: Candidate Generation.

Builds a broad candidate pool using 5 expansion signals.
Key principle: **generate before filtering** — no candidate is rejected
at this stage, only tagged and scored.

Expansion signals:
1. Name Similarity (Lexical)
2. Taxonomy-Based Functional Adjacency (Semantic)
3. BOM Context Similarity (Structural)
4. Supplier-Graph Adjacency (Commercial)
5. Historical Variant Mapping (Normalization)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from src.procurement.substitution_engine import FUNCTIONAL_CATEGORIES

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase


# ── Priority order for candidate_type (lower index = higher priority) ──
_TYPE_PRIORITY = [
    "near_variant",
    "same_family",
    "broader_functional",
    "commercial_adjacency",
    "normalization_variant",
]

# ── Sensitivity flags that trigger pre-validation exclusion ────────────
_SENSITIVITY_FLAGS = {
    "allergen_constraint",
    "regulatory_restriction",
    "kosher_halal",
    "vegan_vegetarian",
    "gmo_restriction",
    "organic_requirement",
}


# ── Text helpers ───────────────────────────────────────────────────────

def _tokenise(name: str) -> set[str]:
    """Split ingredient name into word-level tokens."""
    return set(re.split(r"[\s\-_]+", name.lower().strip()))


def _name_similarity(a: str, b: str) -> float:
    """Combined Jaccard (on word tokens) + SequenceMatcher similarity."""
    ta, tb = _tokenise(a), _tokenise(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.5 * jaccard + 0.5 * seq


def _jaccard(s1: set, s2: set) -> float:
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


# ── Dataclass ──────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """A single substitution candidate with all signal scores and metadata."""

    canonical_name: str
    candidate_type: str  # near_variant | same_family | broader_functional | commercial_adjacency | normalization_variant
    signals: dict = field(default_factory=dict)  # {signal_name: score}
    product_ids: list[int] = field(default_factory=list)
    example_sku: str = ""
    validation_status: str = "unvalidated"  # unvalidated | excluded_pre_validation
    exclusion_reason: str | None = None  # if excluded_pre_validation, which sensitivity flag
    source_signals: list[str] = field(default_factory=list)  # which signals generated this candidate


# ── Category lookup (shared helper) ────────────────────────────────────

class _CategoryLookup:
    """Lazy singleton mapping ingredient fragments -> functional category."""

    def __init__(self) -> None:
        self._lookup: dict[str, str] | None = None

    def _build(self) -> dict[str, str]:
        if self._lookup is not None:
            return self._lookup
        lookup: dict[str, str] = {}
        for cat, members in FUNCTIONAL_CATEGORIES.items():
            for member in members:
                lookup[member.lower()] = cat
        self._lookup = lookup
        return lookup

    def category_for(self, ingredient: str) -> str | None:
        lookup = self._build()
        ing = ingredient.lower().strip()
        if ing in lookup:
            return lookup[ing]
        for fragment, cat in lookup.items():
            if fragment in ing or ing in fragment:
                return cat
        return None

    def members_of(self, category: str) -> list[str]:
        """Return the curated member list for a category."""
        return [m.lower() for m in FUNCTIONAL_CATEGORIES.get(category, [])]


_cat = _CategoryLookup()


# ── Generator ──────────────────────────────────────────────────────────

class CandidateGenerator:
    """Step 2: Build broad candidate pool using 5 expansion signals."""

    def __init__(self, cpg_db: CpgDatabase):
        self.db = cpg_db

    # ------------------------------------------------------------------ public

    def generate(self, context: dict, max_candidates: int = 30) -> list[Candidate]:
        """Generate candidates using all 5 signals, tag with sensitivity flags from context.

        Parameters
        ----------
        context : dict
            Must contain at least ``ingredient_name`` (str).
            Optional keys used:
            - ``product_form`` (str): e.g. "powder", "capsule", "liquid"
            - ``sensitivity_flags`` (list[str]): hard constraints from block 8
            - ``excluded_ingredients`` (list[str]): explicit exclusion list
        max_candidates : int
            Maximum number of candidates to return (after dedup + ranking).
        """
        query = context["ingredient_name"].lower().strip()
        sensitivity_flags: list[str] = context.get("sensitivity_flags", [])
        excluded_ingredients: set[str] = {
            e.lower() for e in context.get("excluded_ingredients", [])
        }
        product_form: str | None = context.get("product_form")

        # Build full ingredient index once
        idx = self.db._ingredient_index()

        # Gather raw candidates from each signal
        pool: dict[str, Candidate] = {}  # canonical_name -> Candidate

        self._signal_name_similarity(query, idx, pool)
        self._signal_functional_adjacency(query, idx, pool, product_form)
        self._signal_bom_context(query, idx, pool)
        self._signal_supplier_adjacency(query, idx, pool)
        self._signal_normalization_variants(query, idx, pool)

        # Apply sensitivity-flag tagging (never removes candidates)
        self._apply_sensitivity_tags(pool, sensitivity_flags, excluded_ingredients)

        # Sort by composite priority then by max signal score
        ranked = sorted(
            pool.values(),
            key=lambda c: (
                _TYPE_PRIORITY.index(c.candidate_type)
                    if c.candidate_type in _TYPE_PRIORITY else len(_TYPE_PRIORITY),
                -(max(c.signals.values()) if c.signals else 0.0),
            ),
        )

        return ranked[:max_candidates]

    # ------------------------------------------------------------------ Signal 1

    def _signal_name_similarity(
        self,
        query: str,
        idx: list[dict],
        pool: dict[str, Candidate],
        threshold: float = 0.25,
    ) -> None:
        """Signal 1: Fuzzy string match on canonical ingredient names."""
        for item in idx:
            cand_name = item["ingredient_name"]
            if cand_name.lower() == query:
                continue
            score = _name_similarity(query, cand_name)
            if score < threshold:
                continue

            # Determine sub-type by score band
            if score >= 0.70:
                ctype = "near_variant"
            elif score >= 0.45:
                ctype = "same_family"
            else:
                ctype = "broader_functional"

            self._merge(
                pool,
                canonical_name=cand_name,
                candidate_type=ctype,
                signal_name="name_similarity",
                signal_score=round(score, 4),
                product_ids=item["product_ids"],
                example_sku=item["example_sku"],
            )

    # ------------------------------------------------------------------ Signal 2

    def _signal_functional_adjacency(
        self,
        query: str,
        idx: list[dict],
        pool: dict[str, Candidate],
        product_form: str | None,
    ) -> None:
        """Signal 2: Taxonomy-based functional adjacency via FUNCTIONAL_CATEGORIES."""
        query_cat = _cat.category_for(query)
        if query_cat is None:
            return

        # Build set of DB ingredient names for fast lookup
        db_names: dict[str, dict] = {
            item["ingredient_name"].lower(): item for item in idx
        }

        # Iterate category members
        cat_members = _cat.members_of(query_cat)
        for member in cat_members:
            if member == query:
                continue

            # Find actual DB ingredients matching this member
            matching_items: list[dict] = []
            if member in db_names:
                matching_items.append(db_names[member])
            else:
                # Partial match against DB names
                for db_name, item in db_names.items():
                    if db_name == query:
                        continue
                    if member in db_name or db_name in member:
                        matching_items.append(item)

            for item in matching_items:
                cand_name = item["ingredient_name"]

                # Product-form narrowing: if the product form is specified,
                # downweight candidates whose names suggest incompatible forms
                form_penalty = 1.0
                if product_form:
                    form_penalty = self._form_compatibility(cand_name, product_form)

                score = round(0.80 * form_penalty, 4)  # base category match score

                self._merge(
                    pool,
                    canonical_name=cand_name,
                    candidate_type="same_family",
                    signal_name="functional_adjacency",
                    signal_score=score,
                    product_ids=item["product_ids"],
                    example_sku=item["example_sku"],
                )

    # ------------------------------------------------------------------ Signal 3

    def _signal_bom_context(
        self,
        query: str,
        idx: list[dict],
        pool: dict[str, Candidate],
        threshold: float = 0.08,
    ) -> None:
        """Signal 3: BOM context similarity — structural role equivalence.

        For each candidate ingredient, compute Jaccard similarity between
        the BOMs that use the query and the BOMs that use the candidate
        (excluding the query/candidate themselves from the ingredient sets).
        """
        query_boms = self.db.ingredient_to_boms().get(query, set())
        if not query_boms:
            return

        bom_sets = self.db.bom_ingredient_sets()
        ing_to_boms = self.db.ingredient_to_boms()

        for item in idx:
            cand_name = item["ingredient_name"]
            if cand_name.lower() == query:
                continue

            cand_boms = ing_to_boms.get(cand_name, set())
            if not cand_boms:
                continue

            # Skip candidates that ALWAYS co-occur (they are complements, not substitutes)
            # Focus on disjoint BOMs — where the candidate appears WITHOUT the query
            disjoint_cand_boms = cand_boms - query_boms
            if not disjoint_cand_boms:
                continue

            # For each disjoint BOM of the candidate, measure structural
            # similarity to the query's BOMs (minus the focal ingredients)
            similarities: list[float] = []
            for cb in disjoint_cand_boms:
                cand_set = bom_sets.get(cb, set()) - {cand_name}
                for qb in query_boms:
                    q_set = bom_sets.get(qb, set()) - {query}
                    similarities.append(_jaccard(cand_set, q_set))

            if not similarities:
                continue

            score = sum(similarities) / len(similarities)
            if score < threshold:
                continue

            self._merge(
                pool,
                canonical_name=cand_name,
                candidate_type="broader_functional",
                signal_name="bom_context",
                signal_score=round(score, 4),
                product_ids=item["product_ids"],
                example_sku=item["example_sku"],
            )

    # ------------------------------------------------------------------ Signal 4

    def _signal_supplier_adjacency(
        self,
        query: str,
        idx: list[dict],
        pool: dict[str, Candidate],
    ) -> None:
        """Signal 4: Supplier-graph adjacency — ingredients carried by the same suppliers."""
        catalog = self.db.get_supplier_catalog()

        # Find suppliers that carry the query ingredient
        query_suppliers: list[str] = []
        for supplier, ingredients in catalog.items():
            lowered = [i.lower() for i in ingredients]
            if query in lowered:
                query_suppliers.append(supplier)

        if not query_suppliers:
            return

        # Build set of all co-carried ingredients
        co_carried: dict[str, int] = {}  # ingredient -> number of shared suppliers
        for supplier in query_suppliers:
            for ing in catalog[supplier]:
                ing_lower = ing.lower()
                if ing_lower == query:
                    continue
                co_carried[ing_lower] = co_carried.get(ing_lower, 0) + 1

        # Index lookup
        name_to_item: dict[str, dict] = {
            item["ingredient_name"].lower(): item for item in idx
        }

        total_suppliers = len(query_suppliers)
        for ing_name, shared_count in co_carried.items():
            if ing_name not in name_to_item:
                continue
            item = name_to_item[ing_name]

            # Score = fraction of query's suppliers that also carry this ingredient
            score = round(shared_count / total_suppliers, 4)
            if score < 0.01:
                continue

            self._merge(
                pool,
                canonical_name=item["ingredient_name"],
                candidate_type="commercial_adjacency",
                signal_name="supplier_adjacency",
                signal_score=score,
                product_ids=item["product_ids"],
                example_sku=item["example_sku"],
            )

    # ------------------------------------------------------------------ Signal 5

    def _signal_normalization_variants(
        self,
        query: str,
        idx: list[dict],
        pool: dict[str, Candidate],
        threshold: float = 0.55,
    ) -> None:
        """Signal 5: Historical variant mapping — canonical names close but SKUs differ."""
        # Find the query item to get its SKU
        query_item: dict | None = None
        for item in idx:
            if item["ingredient_name"].lower() == query:
                query_item = item
                break

        if query_item is None:
            return

        query_sku = query_item["example_sku"].lower()

        for item in idx:
            cand_name = item["ingredient_name"]
            if cand_name.lower() == query:
                continue

            # Canonical name must be fairly close
            name_score = _name_similarity(query, cand_name)
            if name_score < threshold:
                continue

            # But the SKUs should be noticeably different (indicating separate
            # catalog entries that are really variants)
            sku_sim = SequenceMatcher(
                None, query_sku, item["example_sku"].lower()
            ).ratio()

            # We want: similar name BUT different SKU
            # Score = name_similarity * (1 - sku_similarity)
            variant_score = name_score * (1.0 - sku_sim)
            if variant_score < 0.05:
                continue

            self._merge(
                pool,
                canonical_name=cand_name,
                candidate_type="normalization_variant",
                signal_name="normalization_variant",
                signal_score=round(variant_score, 4),
                product_ids=item["product_ids"],
                example_sku=item["example_sku"],
            )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _form_compatibility(ingredient_name: str, product_form: str) -> float:
        """Heuristic penalty when ingredient form doesn't match the product form.

        Returns 1.0 for compatible, 0.5 for uncertain, 0.3 for likely incompatible.
        """
        name_lower = ingredient_name.lower()
        form_lower = product_form.lower()

        # Simple keyword heuristics
        liquid_kw = {"oil", "liquid", "syrup", "extract", "solution", "juice"}
        powder_kw = {"powder", "granule", "granules", "crystalline", "dry"}
        capsule_kw = {"capsule", "gelatin", "softgel", "hypromellose"}

        name_tokens = _tokenise(name_lower)

        if form_lower == "powder":
            if name_tokens & liquid_kw:
                return 0.3
            if name_tokens & powder_kw:
                return 1.0
        elif form_lower in ("liquid", "syrup"):
            if name_tokens & powder_kw:
                return 0.3
            if name_tokens & liquid_kw:
                return 1.0
        elif form_lower in ("capsule", "softgel", "tablet"):
            if name_tokens & capsule_kw:
                return 1.0

        return 0.8  # neutral / unknown

    @staticmethod
    def _merge(
        pool: dict[str, Candidate],
        *,
        canonical_name: str,
        candidate_type: str,
        signal_name: str,
        signal_score: float,
        product_ids: list[int],
        example_sku: str,
    ) -> None:
        """Add or merge a candidate into the pool.

        Deduplication: if the candidate already exists, merge signal scores
        and keep the highest-priority candidate_type.
        """
        key = canonical_name.lower()

        if key in pool:
            existing = pool[key]

            # Merge signal: keep the higher score if signal already present
            if signal_name in existing.signals:
                existing.signals[signal_name] = max(
                    existing.signals[signal_name], signal_score
                )
            else:
                existing.signals[signal_name] = signal_score

            # Track source signals
            if signal_name not in existing.source_signals:
                existing.source_signals.append(signal_name)

            # Merge product_ids (deduplicated)
            seen = set(existing.product_ids)
            for pid in product_ids:
                if pid not in seen:
                    existing.product_ids.append(pid)
                    seen.add(pid)

            # Keep highest-priority candidate_type
            existing_pri = (
                _TYPE_PRIORITY.index(existing.candidate_type)
                if existing.candidate_type in _TYPE_PRIORITY
                else len(_TYPE_PRIORITY)
            )
            new_pri = (
                _TYPE_PRIORITY.index(candidate_type)
                if candidate_type in _TYPE_PRIORITY
                else len(_TYPE_PRIORITY)
            )
            if new_pri < existing_pri:
                existing.candidate_type = candidate_type

        else:
            pool[key] = Candidate(
                canonical_name=canonical_name,
                candidate_type=candidate_type,
                signals={signal_name: signal_score},
                product_ids=list(product_ids),
                example_sku=example_sku,
                validation_status="unvalidated",
                exclusion_reason=None,
                source_signals=[signal_name],
            )

    @staticmethod
    def _apply_sensitivity_tags(
        pool: dict[str, Candidate],
        sensitivity_flags: list[str],
        excluded_ingredients: set[str],
    ) -> None:
        """Tag candidates that violate hard constraints.

        Does NOT remove them — sets validation_status and exclusion_reason.
        """
        if not sensitivity_flags and not excluded_ingredients:
            return

        # Build simple keyword checks per flag
        flag_blocklists: dict[str, set[str]] = {
            "allergen_constraint": {
                "soy", "milk", "whey", "casein", "egg", "peanut",
                "tree nut", "shellfish", "wheat", "gluten",
            },
            "vegan_vegetarian": {
                "gelatin", "bovine", "whey", "casein", "egg",
                "collagen", "fish", "lanolin", "beeswax", "shellac",
                "carmine",
            },
            "kosher_halal": {
                "gelatin", "bovine", "porcine", "carmine",
            },
            "gmo_restriction": {
                "soy", "corn", "canola",
            },
        }

        for cand in pool.values():
            cand_lower = cand.canonical_name.lower()

            # Explicit exclusion list
            if cand_lower in excluded_ingredients:
                cand.validation_status = "excluded_pre_validation"
                cand.exclusion_reason = "explicit_exclusion"
                continue

            # Sensitivity-flag keyword checks
            for flag in sensitivity_flags:
                if flag not in flag_blocklists:
                    continue
                blocklist = flag_blocklists[flag]
                cand_tokens = _tokenise(cand_lower)
                if cand_tokens & blocklist:
                    cand.validation_status = "excluded_pre_validation"
                    cand.exclusion_reason = flag
                    break  # first matching flag wins
