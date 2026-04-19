"""
Agnes Step 3 -- Constraint Inference.

Takes the context (Step 1) and candidates (Step 2) and infers which
constraints should apply to the substitution search.

Key design principle (P1 Fix 1):
    Step 1 provides *structural* data only (co-ingredient patterns, product
    form, sensitivity flags).  Step 3 is where those patterns receive
    *functional* interpretation.  For example, if the dominant co-ingredient
    pattern includes "magnesium stearate" + "microcrystalline cellulose",
    this module infers "tablet excipient cluster" -- meaning candidates must
    be compatible with tablet formulations.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.procurement.substitution_engine import FUNCTIONAL_CATEGORIES

if TYPE_CHECKING:
    from src.procurement.cpg_db import CpgDatabase


# ---------------------------------------------------------------------------
# Pre-built reverse lookup: ingredient fragment -> functional category
# ---------------------------------------------------------------------------

_CATEGORY_LOOKUP: dict[str, str] = {}
for _cat, _members in FUNCTIONAL_CATEGORIES.items():
    for _member in _members:
        _CATEGORY_LOOKUP[_member.lower()] = _cat


def _category_for(ingredient: str) -> str | None:
    """Return the functional category for *ingredient*, or None."""
    ing = ingredient.lower().strip()
    # exact
    if ing in _CATEGORY_LOOKUP:
        return _CATEGORY_LOOKUP[ing]
    # partial (substring in either direction)
    for fragment, cat in _CATEGORY_LOOKUP.items():
        if fragment in ing or ing in fragment:
            return cat
    return None


# ---------------------------------------------------------------------------
# Mapping from product forms to compatible functional roles
# ---------------------------------------------------------------------------

_PRODUCT_FORM_ROLES: dict[str, list[str]] = {
    "tablet": [
        "flow_agent", "binder_filler", "coating", "vitamin_b",
        "vitamin_c", "vitamin_d", "vitamin_e", "vitamin_k",
        "calcium_source", "magnesium_source", "zinc_source", "iron_source",
    ],
    "capsule": [
        "capsule_shell", "flow_agent", "binder_filler",
        "omega_fatty_acid", "vitamin_d", "vitamin_e",
    ],
    "softgel": [
        "capsule_shell", "emulsifier", "omega_fatty_acid",
        "vitamin_d", "vitamin_e", "vitamin_a",
    ],
    "powder": [
        "protein", "sweetener", "flavoring", "thickener_gum",
        "flow_agent", "emulsifier",
    ],
    "gummy": [
        "sweetener", "flavoring", "colouring", "thickener_gum",
        "vitamin_c", "vitamin_d",
    ],
    "liquid": [
        "preservative", "sweetener", "flavoring", "thickener_gum",
        "emulsifier",
    ],
    "bar": [
        "protein", "sweetener", "flavoring", "coating",
        "emulsifier",
    ],
}


# ---------------------------------------------------------------------------
# ConstraintSet dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConstraintSet:
    """The full set of constraints inferred for a substitution search."""

    hard: list[dict] = field(default_factory=list)
    """[{constraint_type, description, source, blocking: True}]"""

    soft: list[dict] = field(default_factory=list)
    """[{constraint_type, description, source, weight: float}]"""

    inferred_functional_role: str | None = None
    """e.g. 'emulsifier', 'flow_agent', 'binder_filler'"""

    formulation_compatibility: list[str] = field(default_factory=list)
    """Compatible product forms derived from context."""

    notes: list[str] = field(default_factory=list)
    """Reasoning trail -- why each constraint was added."""


# ---------------------------------------------------------------------------
# ConstraintInference
# ---------------------------------------------------------------------------

class ConstraintInference:
    """Infer substitution constraints from Step 1 context."""

    def __init__(self, cpg_db: CpgDatabase):
        self.db = cpg_db

    # ---- public API -------------------------------------------------------

    def infer(self, context: dict, candidates: list) -> ConstraintSet:
        """
        Produce a :class:`ConstraintSet` from *context* (Step 1 output) and
        *candidates* (Step 2 output).

        The *context* dict is expected to carry the block keys emitted by
        Step 1 (``formulation_context``, ``sensitivity``, ``compliance``,
        ``product_context``, etc.).
        """
        cs = ConstraintSet()

        # Unpack blocks (tolerant of missing keys)
        sensitivity = context.get("sensitivity", {})
        hard_flags = sensitivity.get("hard_constraints", {})
        soft_flags = sensitivity.get("soft_sensitivities", {})

        compliance = context.get("compliance", {})
        product_ctx = context.get("product_context", {})
        formulation = context.get("formulation_context", {})

        # 1. Hard constraints from sensitivity flags
        self._apply_sensitivity_hard(hard_flags, cs)

        # 2. Regulatory / market constraints
        self._apply_regulatory(compliance, cs)

        # 3. Soft constraints from sensitivity flags
        self._apply_sensitivity_soft(soft_flags, cs)

        # 4. Functional role inference (P1 Fix 1 -- structural -> functional)
        role = self._infer_functional_role(formulation)
        cs.inferred_functional_role = role
        if role:
            cs.soft.append({
                "constraint_type": "functional_role",
                "description": (
                    f"Candidates should serve the '{role}' functional role "
                    f"based on co-ingredient pattern analysis."
                ),
                "source": "formulation_context.dominant_co_ingredient_pattern",
                "weight": 0.8,
            })
            cs.notes.append(
                f"Inferred functional role '{role}' from co-ingredient pattern."
            )

        # 5. Product-form compatibility
        self._apply_product_form(product_ctx, cs)

        return cs

    # ---- hard constraint helpers ------------------------------------------

    def _apply_sensitivity_hard(self, flags: dict, cs: ConstraintSet) -> None:
        if flags.get("allergen_sensitive"):
            allergens = flags["allergen_sensitive"]
            if isinstance(allergens, list):
                desc = f"Exclude allergens: {', '.join(allergens)}"
            else:
                desc = "Exclude common allergens"
            cs.hard.append({
                "constraint_type": "allergen_exclusion",
                "description": desc,
                "source": "sensitivity.hard_constraints.allergen_sensitive",
                "blocking": True,
            })
            cs.notes.append(f"Hard allergen exclusion: {desc}")

        if flags.get("organic_certification_required"):
            cs.hard.append({
                "constraint_type": "organic_required",
                "description": "Substitute must hold organic certification.",
                "source": "sensitivity.hard_constraints.organic_certification_required",
                "blocking": True,
            })
            cs.notes.append("Hard constraint: organic certification required.")

        if flags.get("vegan_vegetarian"):
            cs.hard.append({
                "constraint_type": "vegan_required",
                "description": "Substitute must be vegan/vegetarian compatible.",
                "source": "sensitivity.hard_constraints.vegan_vegetarian",
                "blocking": True,
            })
            cs.notes.append("Hard constraint: vegan/vegetarian required.")

        if flags.get("clean_label"):
            # Clean-label is hard only when the product *declares* a
            # clean-label claim; otherwise it degrades to soft.
            declared = flags["clean_label"]
            if isinstance(declared, dict):
                declared = declared.get("declared_claim", False)
            if declared:
                cs.hard.append({
                    "constraint_type": "clean_label",
                    "description": (
                        "Product carries a clean-label claim -- substitute "
                        "must not introduce synthetic or E-number ingredients."
                    ),
                    "source": "sensitivity.hard_constraints.clean_label",
                    "blocking": True,
                })
                cs.notes.append(
                    "Hard constraint: declared clean-label claim on product."
                )
            else:
                cs.soft.append({
                    "constraint_type": "clean_label",
                    "description": (
                        "Clean-label preference (no declared claim) -- prefer "
                        "natural / recognisable ingredients."
                    ),
                    "source": "sensitivity.hard_constraints.clean_label",
                    "weight": 0.5,
                })
                cs.notes.append(
                    "Soft clean-label preference (no declared claim)."
                )

    # ---- regulatory helpers -----------------------------------------------

    def _apply_regulatory(self, compliance: dict, cs: ConstraintSet) -> None:
        target_market = compliance.get("target_market")
        regime = compliance.get("regulatory_regime")

        if target_market:
            desc_parts = [f"Must comply with {target_market} regulations"]
            if regime:
                desc_parts.append(f"under {regime}")
            cs.hard.append({
                "constraint_type": "regulatory_market",
                "description": " ".join(desc_parts) + ".",
                "source": "compliance.target_market / compliance.regulatory_regime",
                "blocking": True,
            })
            cs.notes.append(
                f"Hard regulatory constraint: market={target_market}, "
                f"regime={regime or 'unspecified'}."
            )

    # ---- soft constraint helpers ------------------------------------------

    def _apply_sensitivity_soft(self, flags: dict, cs: ConstraintSet) -> None:
        if flags.get("premium_positioned"):
            cs.soft.append({
                "constraint_type": "premium_grade",
                "description": (
                    "Product is premium-positioned -- prefer higher-grade, "
                    "branded, or clinically studied ingredient forms."
                ),
                "source": "sensitivity.soft_sensitivities.premium_positioned",
                "weight": 0.6,
            })
            cs.notes.append("Soft: premium grade preferred.")

        if flags.get("child_focused"):
            cs.soft.append({
                "constraint_type": "child_safety",
                "description": (
                    "Product targets children -- substitute must be age-"
                    "appropriate (dosage, form, no choking hazard additives)."
                ),
                "source": "sensitivity.soft_sensitivities.child_focused",
                "weight": 0.9,
            })
            cs.notes.append("Soft (high weight): child-safety considerations.")

    # ---- product-form compatibility ---------------------------------------

    def _apply_product_form(self, product_ctx: dict, cs: ConstraintSet) -> None:
        product_form = (product_ctx.get("product_form") or "").lower().strip()
        if not product_form:
            return

        compatible_roles = _PRODUCT_FORM_ROLES.get(product_form, [])
        if compatible_roles:
            cs.formulation_compatibility = list(compatible_roles)
            cs.soft.append({
                "constraint_type": "product_form_compatibility",
                "description": (
                    f"Product form is '{product_form}' -- candidates should "
                    f"be compatible with {product_form} formulations."
                ),
                "source": "product_context.product_form",
                "weight": 0.7,
            })
            cs.notes.append(
                f"Product form '{product_form}' mapped to compatible roles: "
                f"{', '.join(compatible_roles)}."
            )
        else:
            cs.notes.append(
                f"Product form '{product_form}' has no predefined role mapping; "
                f"no product-form constraint applied."
            )

    # ---- functional role inference (P1 Fix 1) -----------------------------

    def _infer_functional_role(
        self, formulation_context: dict
    ) -> str | None:
        """
        Derive the functional role of the *target* ingredient from its
        co-ingredient neighbourhood.

        Strategy:
        1. Read ``dominant_co_ingredient_pattern`` from *formulation_context*.
        2. Map each co-ingredient to its functional category.
        3. The most frequent category among co-ingredients tells us what
           *environment* the target ingredient lives in.  The target's own
           role is likely complementary (e.g. in a tablet excipient cluster
           the target is probably a flow_agent or binder_filler).
        4. Return the inferred role string, or None if inference fails.
        """
        co_ingredients: list[str] = formulation_context.get(
            "dominant_co_ingredient_pattern", []
        )
        if not co_ingredients:
            return None

        # Map each co-ingredient to its category
        cat_counts: Counter[str] = Counter()
        for ing in co_ingredients:
            cat = _category_for(ing)
            if cat:
                cat_counts[cat] += 1

        if not cat_counts:
            return None

        # The most common category among neighbours is the "environment".
        # Heuristic: if a single category dominates, the target likely
        # plays a *supporting* role from a different category.  If the
        # categories are diverse (many distinct ones), the target is
        # likely in the dominant category itself.
        dominant_cat, dominant_count = cat_counts.most_common(1)[0]
        total_mapped = sum(cat_counts.values())

        # Build the cluster description note
        cluster_desc = ", ".join(
            f"{cat}({n})" for cat, n in cat_counts.most_common()
        )

        if len(cat_counts) == 1:
            # All co-ingredients share a category -- target is probably
            # from a complementary functional role.
            complementary = _COMPLEMENTARY_ROLES.get(dominant_cat)
            if complementary:
                role = complementary
                self._add_cluster_note_to = (
                    f"All co-ingredients are '{dominant_cat}'; inferred "
                    f"complementary role '{role}'. Cluster: [{cluster_desc}]."
                )
                return role
            # Fallback: target is in the same category
            return dominant_cat

        # Multiple categories -- the dominant one hints at the formulation
        # type.  Map the cluster to an inferred role.
        dominance_ratio = dominant_count / total_mapped

        if dominance_ratio >= 0.5:
            # Strong dominance -- look up complementary role
            complementary = _COMPLEMENTARY_ROLES.get(dominant_cat)
            if complementary:
                return complementary
            return dominant_cat
        else:
            # Diverse cluster -- target is likely in the dominant category
            return dominant_cat


# ---------------------------------------------------------------------------
# Complementary role mapping
# ---------------------------------------------------------------------------
# When co-ingredients are all in category X, the target ingredient is likely
# serving a complementary role Y.

_COMPLEMENTARY_ROLES: dict[str, str] = {
    "binder_filler": "flow_agent",
    "flow_agent": "binder_filler",
    "capsule_shell": "flow_agent",
    "protein": "sweetener",
    "sweetener": "flavoring",
    "flavoring": "sweetener",
    "coating": "binder_filler",
    "emulsifier": "thickener_gum",
    "thickener_gum": "emulsifier",
    "preservative": "flavoring",
    "colouring": "flavoring",
    # Vitamin / mineral clusters: target is probably an excipient
    "vitamin_a": "flow_agent",
    "vitamin_b": "flow_agent",
    "vitamin_c": "flow_agent",
    "vitamin_d": "capsule_shell",
    "vitamin_e": "capsule_shell",
    "vitamin_k": "flow_agent",
    "calcium_source": "binder_filler",
    "magnesium_source": "flow_agent",
    "zinc_source": "flow_agent",
    "iron_source": "flow_agent",
    "omega_fatty_acid": "capsule_shell",
}
