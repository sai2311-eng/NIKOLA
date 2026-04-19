"""
Substitution Engine — the hackathon centrepiece.

Identifies functionally equivalent CPG raw materials using three signals:
1. Name similarity (Jaccard on word tokens)
2. BOM co-occurrence (appear in same "slot" across similar formulations)
3. Functional category clustering (lecithins, proteins, sweeteners, etc.)

Variant filtering: different forms of the same base ingredient (e.g.
magnesium oxide vs magnesium citrate) are NOT treated as substitutes.

Web fallback: when no local substitutes are found, the engine searches
the web for alternative ingredients.
"""

from __future__ import annotations

import re
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import requests

from .evidence import EvidenceTrail

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .cpg_db import CpgDatabase


# ── Functional categories (curated domain knowledge) ───────────────────
FUNCTIONAL_CATEGORIES: dict[str, list[str]] = {
    "emulsifier": [
        "lecithin", "soy lecithin", "sunflower lecithin", "polysorbate",
        "mono and diglycerides", "glyceryl monostearate",
    ],
    "protein": [
        "whey protein isolate", "whey protein concentrate",
        "hydrolyzed whey protein", "casein", "pea protein",
        "soy protein isolate", "collagen peptides", "egg albumin",
    ],
    "sweetener": [
        "sucralose", "acesulfame potassium", "stevia",
        "rebaudioside a", "monk fruit", "erythritol",
        "sugar", "fructose", "dextrose", "maltodextrin",
    ],
    "flow_agent": [
        "magnesium stearate", "stearic acid", "silicon dioxide",
        "calcium stearate", "talc", "microcrystalline cellulose",
    ],
    "capsule_shell": [
        "gelatin", "hypromellose", "softgel capsule bovine gelatin",
        "softgel capsule", "vegetable cellulose capsule",
        "hydroxypropyl methylcellulose",
    ],
    "binder_filler": [
        "cellulose", "cellulose gel", "cellulose gum",
        "croscarmellose sodium", "hydroxypropyl cellulose",
        "dicalcium phosphate", "calcium carbonate",
    ],
    "coating": [
        "polyvinyl alcohol", "polyethylene glycol",
        "titanium dioxide", "shellac", "carnauba wax",
    ],
    "vitamin_a": ["vitamin a", "retinyl palmitate", "beta carotene", "retinol"],
    "vitamin_b": [
        "thiamine", "riboflavin", "niacin", "niacinamide",
        "pyridoxine", "vitamin b6", "vitamin b12",
        "cyanocobalamin", "methylcobalamin", "folic acid",
        "folate", "d calcium pantothenate", "pantothenic acid",
        "biotin",
    ],
    "vitamin_c": ["vitamin c", "ascorbic acid", "calcium ascorbate", "sodium ascorbate"],
    "vitamin_d": ["vitamin d3", "vitamin d3 cholecalciferol", "cholecalciferol", "vitamin d2"],
    "vitamin_e": [
        "vitamin e", "d alpha tocopherol", "d alpha tocopheryl succinate",
        "d alpha tocopheryl acetate", "mixed tocopherols",
    ],
    "vitamin_k": ["vitamin k", "vitamin k2", "phytonadione", "menaquinone"],
    "calcium_source": [
        "calcium carbonate", "calcium citrate", "calcium phosphate",
        "dicalcium phosphate", "calcium lactate gluconate",
        "calcium ascorbate",
    ],
    "magnesium_source": [
        "magnesium oxide", "magnesium citrate", "magnesium stearate",
        "magnesium glycinate", "magnesium lactate", "magnesium silicate",
    ],
    "zinc_source": ["zinc", "zinc oxide", "zinc citrate", "zinc gluconate", "zinc picolinate"],
    "iron_source": ["ferrous fumarate", "ferrous sulfate", "iron", "ferrous bisglycinate"],
    "omega_fatty_acid": [
        "fish oil", "omega 3", "epa", "dha",
        "flaxseed oil", "algal oil", "cod liver oil",
    ],
    "preservative": [
        "citric acid", "ascorbic acid", "tocopherols",
        "rosemary extract", "sodium benzoate", "potassium sorbate",
    ],
    "flavoring": [
        "natural flavor", "natural flavors", "artificial flavor",
        "natural tangerine flavor", "other natural flavors",
        "chocolate flavor", "cocoa processed with alkali",
        "vanilla extract",
    ],
    "thickener_gum": [
        "xanthan gum", "guar gum", "carrageenan", "cellulose gum",
        "acacia gum", "pectin", "agar",
    ],
    "colouring": [
        "beet extract", "beta carotene", "titanium dioxide",
        "annatto", "caramel color", "turmeric",
    ],
}


def _tokenise(name: str) -> set[str]:
    """Split ingredient name to word-level tokens."""
    return set(re.split(r"[\s\-_]+", name.lower().strip()))


# Salt / chemical-form modifiers — stripping these reveals the "base" ingredient.
_SALT_MODIFIERS = {
    "oxide", "citrate", "stearate", "glycinate", "lactate", "silicate",
    "carbonate", "phosphate", "gluconate", "picolinate", "fumarate",
    "sulfate", "bisglycinate", "palmitate", "acetate", "succinate",
    "ascorbate", "chelate", "orotate", "threonate", "taurate",
    "malate", "aspartate", "chloride", "hydroxide",
    "isolate", "concentrate", "hydrolyzed", "hydrochloride",
    "monostearate", "diglycerides", "taurinate", "arginate",
    "lysinate", "pidolate", "l",
}


def _is_variant(query: str, candidate: str) -> bool:
    """Return True if *candidate* is just a different form/salt of *query*.

    Examples that ARE variants (should be excluded as substitutes):
      - "magnesium citrate"  vs  "magnesium oxide"   (same mineral, different salt)
      - "calcium carbonate"  vs  "calcium citrate"
      - "vitamin e"          vs  "vitamin e"          (exact match, caught earlier)

    Examples that are NOT variants (legitimate substitutes):
      - "magnesium stearate" vs  "silicon dioxide"    (different base)
      - "soy lecithin"       vs  "sunflower lecithin" (different source)
      - "vitamin c"          vs  "vitamin d3"         (different vitamin)
    """
    q = query.lower().strip()
    c = candidate.lower().strip()

    # Direct substring: "magnesium" matches "magnesium citrate"
    if q in c or c in q:
        return True

    # Strip salt/form modifiers and compare base tokens
    q_base = _tokenise(q) - _SALT_MODIFIERS
    c_base = _tokenise(c) - _SALT_MODIFIERS

    # If either base is empty after stripping (pure modifier names), skip
    if not q_base or not c_base:
        return False

    # Same base = variant (e.g. both reduce to {"magnesium"})
    return q_base == c_base


def _name_similarity(a: str, b: str) -> float:
    """Combined Jaccard + SequenceMatcher similarity."""
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


class SubstitutionEngine:
    """Find functionally equivalent CPG ingredients."""

    def __init__(self, cpg_db: CpgDatabase):
        self.db = cpg_db
        self._cat_lookup: dict[str, str] | None = None

    # ── Category lookup ────────────────────────────────────────────────

    def _build_category_lookup(self) -> dict[str, str]:
        """ingredient_fragment -> category name."""
        if self._cat_lookup is not None:
            return self._cat_lookup
        lookup: dict[str, str] = {}
        for cat, members in FUNCTIONAL_CATEGORIES.items():
            for member in members:
                lookup[member.lower()] = cat
        self._cat_lookup = lookup
        return lookup

    def _category_for(self, ingredient: str) -> str | None:
        lookup = self._build_category_lookup()
        ing = ingredient.lower().strip()
        # exact match first
        if ing in lookup:
            return lookup[ing]
        # partial match
        for fragment, cat in lookup.items():
            if fragment in ing or ing in fragment:
                return cat
        return None

    # ── Functional category clustering ─────────────────────────────────

    def get_functional_categories(self) -> dict[str, list[str]]:
        """Map each category to actual ingredients present in the database."""
        idx = self.db._ingredient_index()
        all_names = {item["ingredient_name"] for item in idx}

        result: dict[str, list[str]] = defaultdict(list)
        for name in sorted(all_names):
            cat = self._category_for(name)
            if cat:
                result[cat].append(name)
        return dict(result)

    # ── Core substitution logic ────────────────────────────────────────

    def find_substitutes(
        self,
        ingredient_name: str,
        max_results: int = 10,
        min_similarity: float = 0.20,
        evidence: EvidenceTrail | None = None,
    ) -> list[dict]:
        """
        Find substitutable ingredients using three signals:
        1. Name similarity
        2. BOM co-occurrence (appear in same functional slot)
        3. Functional category match
        """
        query = ingredient_name.lower().strip()
        query_cat = self._category_for(query)
        query_boms = self.db.ingredient_to_boms().get(query, set())
        bom_sets = self.db.bom_ingredient_sets()

        idx = self.db._ingredient_index()
        candidates: list[dict] = []

        for item in idx:
            cand = item["ingredient_name"]
            if cand.lower() == query:
                continue

            # ── Variant filter: skip different forms of the same base ingredient
            # e.g. "magnesium oxide" is NOT a substitute for "magnesium citrate"
            if _is_variant(query, cand):
                continue

            # Signal 1: name similarity
            name_sim = _name_similarity(query, cand)

            # Signal 2: BOM co-occurrence analysis
            cand_boms = self.db.ingredient_to_boms().get(cand, set())
            # Ingredients are substitutable if they appear in SIMILAR boms
            # but rarely in the SAME bom (they replace each other)
            shared_boms = query_boms & cand_boms       # same BOMs
            disjoint_boms = query_boms ^ cand_boms      # one or the other

            bom_cooccurrence = 0.0
            if disjoint_boms:
                # For each BOM with the candidate but not the query,
                # check how similar that BOM is to query's BOMs
                similarities = []
                for cb in cand_boms - query_boms:
                    cand_set = bom_sets.get(cb, set()) - {cand}
                    for qb in query_boms:
                        q_set = bom_sets.get(qb, set()) - {query}
                        similarities.append(_jaccard(cand_set, q_set))
                if similarities:
                    bom_cooccurrence = sum(similarities) / len(similarities)

            # Signal 3: same functional category
            cand_cat = self._category_for(cand)
            cat_match = 1.0 if (query_cat and cand_cat and query_cat == cand_cat) else 0.0

            # Weighted composite
            score = (0.30 * name_sim) + (0.40 * bom_cooccurrence) + (0.30 * cat_match)

            if score < min_similarity:
                continue

            # Gather supplier info
            suppliers = []
            for pid in item["product_ids"][:3]:  # sample up to 3 product IDs
                for s in self.db.get_suppliers_for_product(pid):
                    if s["Name"] not in suppliers:
                        suppliers.append(s["Name"])

            # Companies using this candidate
            demand = self.db.get_demand_map().get(cand, [])
            companies = sorted(set(d["company"] for d in demand))

            candidates.append({
                "ingredient": cand,
                "similarity_score": round(score, 3),
                "evidence": {
                    "name_similarity": round(name_sim, 3),
                    "bom_cooccurrence": round(bom_cooccurrence, 3),
                    "functional_category": query_cat or "uncategorized",
                    "category_match": cat_match > 0,
                    "shared_bom_count": len(shared_boms),
                    "disjoint_bom_count": len(disjoint_boms),
                },
                "suppliers": suppliers,
                "used_by_companies": companies,
                "bom_count": len(cand_boms),
            })

        # Sort by composite score descending
        candidates.sort(key=lambda c: c["similarity_score"], reverse=True)
        results = candidates[:max_results]

        # ── Web fallback: if no local substitutes, search the web ────────
        if not results:
            web_results = _web_search_substitutes(ingredient_name)
            if web_results:
                results = web_results[:max_results]

        # Record evidence
        if evidence and results:
            evidence.add(
                stage="substitution",
                claim=f"Found {len(results)} substitutes for '{ingredient_name}'",
                source="SubstitutionEngine (name + BOM co-occurrence + category)",
                confidence=results[0]["similarity_score"] if results else 0,
                data_ref={"top_substitute": results[0]["ingredient"] if results else None},
            )
            for r in results[:3]:
                reasons = []
                if r["evidence"].get("category_match"):
                    reasons.append(f"same category: {r['evidence'].get('functional_category', '')}")
                if r["evidence"].get("name_similarity", 0) > 0.4:
                    reasons.append("similar name")
                if r["evidence"].get("bom_cooccurrence", 0) > 0.1:
                    reasons.append(
                        f"BOM overlap ({r['evidence'].get('disjoint_bom_count', 0)} related formulations)"
                    )
                if r["evidence"].get("source") == "web_search":
                    reasons.append("web search result")
                evidence.add(
                    stage="substitution",
                    claim=f"'{r['ingredient']}' is a substitute ({r['similarity_score']:.0%}): {', '.join(reasons) or 'composite match'}",
                    source=r["evidence"].get("source", "BOM structural analysis + functional categories"),
                    confidence=r["similarity_score"],
                )

        return results


# ── Web fallback for substitutes ──────────────────────────────────────────

def _web_search_substitutes(ingredient_name: str) -> list[dict]:
    """Search the web for substitute ingredients when local DB has none.

    Uses DuckDuckGo HTML search to find alternative ingredients that can
    replace the given ingredient in CPG formulations.
    """
    query = f"{ingredient_name} substitute alternative ingredient supplement"
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return []

        html = resp.text
        results: list[dict] = []

        # Extract snippet texts from DuckDuckGo results
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL
        )

        # Parse potential substitute names from snippets
        seen: set[str] = set()
        substitute_patterns = [
            # "X can be substituted with Y"
            r'(?:substitut\w+|replac\w+|alternativ\w+)\s+(?:with|by|for|to|is)\s+([A-Za-z][A-Za-z\s]{2,30})',
            # "Y is a substitute for X"
            r'([A-Za-z][A-Za-z\s]{2,30})\s+(?:is|are)\s+(?:a\s+)?(?:substitute|alternative|replacement)',
            # "instead of X, use Y"
            r'(?:use|try|consider)\s+([A-Za-z][A-Za-z\s]{2,30})\s+(?:instead|as)',
        ]

        for snippet in snippets[:10]:
            # Strip HTML tags
            clean = re.sub(r"<[^>]+>", "", snippet).strip()
            for pattern in substitute_patterns:
                matches = re.findall(pattern, clean, re.IGNORECASE)
                for match in matches:
                    name = match.strip().lower()
                    # Filter out generic words and the original ingredient
                    if (
                        len(name) > 3
                        and name not in seen
                        and ingredient_name.lower() not in name
                        and name not in ingredient_name.lower()
                        and not _is_variant(ingredient_name, name)
                        and name not in {"this", "that", "these", "those", "they", "them",
                                        "some", "other", "another", "also", "here"}
                    ):
                        seen.add(name)
                        results.append({
                            "ingredient": name,
                            "similarity_score": 0.35,  # lower confidence for web results
                            "evidence": {
                                "name_similarity": 0.0,
                                "bom_cooccurrence": 0.0,
                                "functional_category": "web_search",
                                "category_match": False,
                                "shared_bom_count": 0,
                                "disjoint_bom_count": 0,
                                "source": "web_search",
                            },
                            "suppliers": [],
                            "used_by_companies": [],
                            "bom_count": 0,
                        })

        return results

    except Exception as e:
        logger.warning(f"Web substitute search failed for '{ingredient_name}': {e}")
        return []
