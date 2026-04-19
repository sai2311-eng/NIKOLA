"""
Supplier Risk & Compliance Scoring Engine

Evaluates suppliers against 8 international standards across two categories:
  - Legal / Mandatory (30 pts): REACH, CPSC, CE Mark
  - Quality / Voluntary (50 pts): Codex Alimentarius, HACCP, ASTM, ISO 10377, IEC

Total score: 80 points.

Each standard is worth 10 points, scored on evidence quality:
  10 = Third-party certified (SGS, TÜV, Bureau Veritas, Intertek, etc.)
   7 = Certificate provided but not independently verified
   4 = Self-declared compliance (no certificate)
   0 = No evidence / not applicable

Red flags:
  - Self-declaration without third-party certificate
  - Expired certifications
  - REACH claim without Material Safety Data Sheet (MSDS)
  - Certificate name mismatch with supplier name
"""

from __future__ import annotations

import re
import time
import random
from dataclasses import dataclass, field
from typing import Optional


# ── Standard definitions ─────────────────────────────────────────────────────

STANDARDS = [
    {
        "id": "codex",
        "name": "Codex Alimentarius",
        "full_name": "Codex Alimentarius (FAO/WHO)",
        "category": "quality",
        "points": 10,
        "description": "International food safety & quality standards",
        "verification_db": "FAO/WHO Codex Alimentarius",
        "verification_url": "https://www.fao.org/fao-who-codexalimentarius/",
        "keywords": ["codex alimentarius", "codex", "fao/who", "fao who"],
    },
    {
        "id": "haccp",
        "name": "HACCP",
        "full_name": "HACCP (Hazard Analysis Critical Control Points)",
        "category": "quality",
        "points": 10,
        "description": "Global food safety management system",
        "verification_db": "BRCGS Certificated Sites Directory",
        "verification_url": "https://brcdirectory.co.uk/",
        "keywords": ["haccp", "hazard analysis", "critical control point"],
    },
    {
        "id": "reach",
        "name": "REACH",
        "full_name": "REACH Regulation (EU ECHA)",
        "category": "legal",
        "points": 10,
        "description": "EU chemical restrictions in consumer goods (mandatory)",
        "verification_db": "ECHA Information on Chemicals",
        "verification_url": "https://echa.europa.eu/information-on-chemicals",
        "keywords": ["reach", "echa", "svhc", "reach regulation", "reach compliant"],
    },
    {
        "id": "cpsc",
        "name": "CPSC",
        "full_name": "CPSC Regulations (USA)",
        "category": "legal",
        "points": 10,
        "description": "US Consumer Product Safety Commission rules (mandatory)",
        "verification_db": "CPSC Accepted Testing Labs",
        "verification_url": "https://www.cpsc.gov/cgi-bin/labsearch/",
        "keywords": ["cpsc", "consumer product safety", "cpc", "gcc",
                     "general certificate of conformity"],
    },
    {
        "id": "ce_mark",
        "name": "CE Mark",
        "full_name": "CE Marking (EU)",
        "category": "legal",
        "points": 10,
        "description": "EU conformity marking (mandatory for applicable products)",
        "verification_db": "NANDO Database",
        "verification_url": "https://ec.europa.eu/growth/tools-databases/nando/",
        "keywords": ["ce mark", "ce marking", "ce certified", "declaration of conformity",
                     "notified body"],
    },
    {
        "id": "astm",
        "name": "ASTM",
        "full_name": "ASTM International Standards",
        "category": "quality",
        "points": 10,
        "description": "Voluntary consensus standards for materials & products",
        "verification_db": "ASTM Certified Companies Directory",
        "verification_url": "https://www.astm.org/",
        "keywords": ["astm", "astm international", "astm standard"],
    },
    {
        "id": "iso",
        "name": "ISO",
        "full_name": "ISO 10377 / ISO 9001",
        "category": "quality",
        "points": 10,
        "description": "Consumer product safety & quality management",
        "verification_db": "IAF CertSearch / Accreditation Body",
        "verification_url": "https://www.iafcertsearch.org/",
        "keywords": ["iso 10377", "iso 9001", "iso certified", "iso certification"],
    },
    {
        "id": "iec",
        "name": "IEC",
        "full_name": "IEC (International Electrotechnical Commission)",
        "category": "quality",
        "points": 10,
        "description": "Electrical/electronic product safety standards",
        "verification_db": "IECEE CB Certificate Search",
        "verification_url": "https://www.iecee.org/certificates",
        "keywords": ["iec", "iecee", "cb test certificate", "cb scheme",
                     "electrotechnical"],
    },
]

# Accredited certification bodies (high trust)
ACCREDITED_BODIES = {
    "sgs", "tuv", "tüv", "tuv rheinland", "tuv sud", "bureau veritas",
    "intertek", "bsi", "dnv", "dekra", "ul", "underwriters laboratories",
    "lloyd's", "lloyds", "applus", "kiwa", "nemko", "csa", "nsf",
    "eurofins", "qima", "rina", "iqnet",
}

# Evidence levels
EVIDENCE_THIRD_PARTY = "third_party"
EVIDENCE_CERTIFICATE = "certificate_unverified"
EVIDENCE_SELF_DECLARED = "self_declared"
EVIDENCE_NONE = "none"
EVIDENCE_EXPIRED = "expired"

EVIDENCE_SCORES = {
    EVIDENCE_THIRD_PARTY: 10,
    EVIDENCE_CERTIFICATE: 7,
    EVIDENCE_SELF_DECLARED: 4,
    EVIDENCE_EXPIRED: 2,
    EVIDENCE_NONE: 0,
}


@dataclass
class StandardResult:
    standard_id: str
    standard_name: str
    category: str  # "legal" or "quality"
    max_points: int
    score: int
    evidence_level: str  # one of EVIDENCE_*
    details: str = ""
    red_flags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    verification_db: str = ""
    verification_url: str = ""

    def to_dict(self) -> dict:
        return {
            "standard_id": self.standard_id,
            "standard_name": self.standard_name,
            "category": self.category,
            "max_points": self.max_points,
            "score": self.score,
            "evidence_level": self.evidence_level,
            "details": self.details,
            "red_flags": self.red_flags,
            "sources": self.sources,
            "verification_db": self.verification_db,
            "verification_url": self.verification_url,
        }


@dataclass
class ComplianceReport:
    supplier_name: str
    total_score: int
    max_score: int  # 80
    legal_score: int
    legal_max: int  # 30
    quality_score: int
    quality_max: int  # 50
    standards: list[StandardResult]
    red_flags: list[str]
    risk_level: str  # "low", "medium", "high", "critical"

    def to_dict(self) -> dict:
        return {
            "supplier_name": self.supplier_name,
            "total_score": self.total_score,
            "max_score": self.max_score,
            "legal_score": self.legal_score,
            "legal_max": self.legal_max,
            "quality_score": self.quality_score,
            "quality_max": self.quality_max,
            "risk_level": self.risk_level,
            "red_flags": self.red_flags,
            "standards": [s.to_dict() for s in self.standards],
        }


def _search_web_for_compliance(supplier_name: str, standard: dict) -> dict:
    """
    Search the web for evidence of a supplier's compliance with a standard.
    Returns dict with keys: evidence_level, details, red_flags, sources.
    """
    try:
        from data_collection.search_engine import multi_engine_search
    except ImportError:
        return {
            "evidence_level": EVIDENCE_NONE,
            "details": "Search engine not available",
            "red_flags": [],
            "sources": [],
        }

    keywords = standard["keywords"]
    query = f'"{supplier_name}" {keywords[0]} certification'

    try:
        results = multi_engine_search(query, max_per_engine=5,
                                      use_ddg=True, use_bing=True,
                                      use_google=False)
    except Exception:
        results = []

    if not results:
        return {
            "evidence_level": EVIDENCE_NONE,
            "details": f"No web evidence found for {standard['name']}",
            "red_flags": [],
            "sources": [],
        }

    # Analyze results
    sources = [r.get("url", "") for r in results[:5] if r.get("url")]
    all_text = " ".join(
        f"{r.get('title', '')} {r.get('snippet', '')}".lower()
        for r in results
    )

    red_flags = []
    evidence_level = EVIDENCE_NONE
    details = ""

    # Check for accredited body mentions
    found_body = None
    for body in ACCREDITED_BODIES:
        if body in all_text:
            found_body = body
            break

    # Check for certificate / certification mentions
    has_cert_mention = any(
        w in all_text for w in [
            "certified", "certification", "certificate",
            "accredited", "audited", "verified",
        ]
    )

    # Check for self-declaration signals
    has_self_decl = any(
        w in all_text for w in [
            "self-declared", "self declared", "we comply",
            "we follow", "we adhere", "in compliance",
        ]
    )

    # Check for expiry signals
    has_expired = any(
        w in all_text for w in [
            "expired", "lapsed", "no longer valid", "revoked",
            "suspended", "withdrawn",
        ]
    )

    # Determine evidence level
    if has_expired:
        evidence_level = EVIDENCE_EXPIRED
        details = f"Certification appears expired or revoked"
        red_flags.append(f"Expired/revoked {standard['name']} certification")
    elif found_body and has_cert_mention:
        evidence_level = EVIDENCE_THIRD_PARTY
        details = f"Third-party certification found (body: {found_body.upper()})"
    elif has_cert_mention:
        evidence_level = EVIDENCE_CERTIFICATE
        details = f"Certificate mentioned but issuing body not verified"
    elif has_self_decl:
        evidence_level = EVIDENCE_SELF_DECLARED
        details = f"Self-declared compliance (no third-party certificate found)"
        red_flags.append(
            f"{standard['name']}: Self-declaration only — "
            f"no certificate from accredited body (SGS, TÜV, etc.)"
        )
    elif any(kw in all_text for kw in keywords):
        evidence_level = EVIDENCE_SELF_DECLARED
        details = f"Standard mentioned in supplier context but no certificate evidence"
        red_flags.append(f"{standard['name']}: Mentioned but not certified")
    else:
        evidence_level = EVIDENCE_NONE
        details = f"No evidence of {standard['name']} compliance found"

    # REACH-specific: check for MSDS
    if standard["id"] == "reach" and evidence_level >= EVIDENCE_SELF_DECLARED:
        has_msds = any(
            w in all_text for w in ["msds", "material safety data sheet", "sds",
                                     "safety data sheet"]
        )
        if not has_msds:
            red_flags.append(
                "REACH: Claims compliance but no Material Safety Data Sheet (MSDS) found"
            )

    return {
        "evidence_level": evidence_level,
        "details": details,
        "red_flags": red_flags,
        "sources": sources,
    }


def evaluate_supplier(
    supplier_name: str,
    target_market: str = "both",
    progress_callback=None,
) -> ComplianceReport:
    """
    Run the full compliance evaluation for a supplier.
    Searches the web for evidence of each standard.

    Args:
        supplier_name: Company name to evaluate
        target_market: 'usa', 'eu', or 'both'
        progress_callback: Optional callable(step, total, message)

    Returns:
        ComplianceReport with scores and risk flags.
    """
    results: list[StandardResult] = []
    all_red_flags: list[str] = []

    total_steps = len(STANDARDS)

    for i, std in enumerate(STANDARDS):
        if progress_callback:
            progress_callback(i, total_steps,
                              f"Checking {std['name']}...")

        # Search web for evidence
        evidence = _search_web_for_compliance(supplier_name, std)

        score = EVIDENCE_SCORES.get(evidence["evidence_level"], 0)

        result = StandardResult(
            standard_id=std["id"],
            standard_name=std["full_name"],
            category=std["category"],
            max_points=std["points"],
            score=score,
            evidence_level=evidence["evidence_level"],
            details=evidence["details"],
            red_flags=evidence["red_flags"],
            sources=evidence["sources"],
            verification_db=std["verification_db"],
            verification_url=std["verification_url"],
        )
        results.append(result)
        all_red_flags.extend(evidence["red_flags"])

        # Polite delay between searches
        time.sleep(random.uniform(0.3, 0.8))

    if progress_callback:
        progress_callback(total_steps, total_steps, "Scoring complete")

    # Calculate scores
    legal_score = sum(r.score for r in results if r.category == "legal")
    quality_score = sum(r.score for r in results if r.category == "quality")
    total_score = legal_score + quality_score

    legal_max = sum(r.max_points for r in results if r.category == "legal")
    quality_max = sum(r.max_points for r in results if r.category == "quality")

    # Determine risk level
    pct = total_score / 80 * 100 if total_score > 0 else 0
    if pct >= 75:
        risk_level = "low"
    elif pct >= 50:
        risk_level = "medium"
    elif pct >= 25:
        risk_level = "high"
    else:
        risk_level = "critical"

    # Additional red flags
    if legal_score == 0:
        all_red_flags.insert(0, "CRITICAL: Zero legal compliance evidence found")

    self_decl_count = sum(
        1 for r in results if r.evidence_level == EVIDENCE_SELF_DECLARED
    )
    if self_decl_count >= 3:
        all_red_flags.append(
            f"{self_decl_count} standards are self-declared only — "
            f"high risk of unverified claims"
        )

    return ComplianceReport(
        supplier_name=supplier_name,
        total_score=total_score,
        max_score=80,
        legal_score=legal_score,
        legal_max=legal_max,
        quality_score=quality_score,
        quality_max=quality_max,
        standards=results,
        red_flags=all_red_flags,
        risk_level=risk_level,
    )


def score_from_manual_input(evidence_entries: dict[str, str]) -> ComplianceReport:
    """
    Score a supplier based on manually provided evidence levels.

    Args:
        evidence_entries: dict mapping standard_id to evidence level
            e.g. {"reach": "third_party", "cpsc": "self_declared", ...}

    Returns:
        ComplianceReport
    """
    results = []
    all_flags = []

    for std in STANDARDS:
        sid = std["id"]
        ev = evidence_entries.get(sid, EVIDENCE_NONE)
        score = EVIDENCE_SCORES.get(ev, 0)

        flags = []
        if ev == EVIDENCE_SELF_DECLARED:
            flags.append(
                f"{std['name']}: Self-declaration only — no third-party certificate"
            )
        if ev == EVIDENCE_EXPIRED:
            flags.append(f"{std['name']}: Certification expired")

        results.append(StandardResult(
            standard_id=sid,
            standard_name=std["full_name"],
            category=std["category"],
            max_points=std["points"],
            score=score,
            evidence_level=ev,
            details=f"Manually entered: {ev}",
            red_flags=flags,
            sources=[],
            verification_db=std["verification_db"],
            verification_url=std["verification_url"],
        ))
        all_flags.extend(flags)

    legal_score = sum(r.score for r in results if r.category == "legal")
    quality_score = sum(r.score for r in results if r.category == "quality")
    total = legal_score + quality_score

    pct = total / 80 * 100
    risk_level = ("low" if pct >= 75 else "medium" if pct >= 50
                  else "high" if pct >= 25 else "critical")

    return ComplianceReport(
        supplier_name="Manual Entry",
        total_score=total,
        max_score=80,
        legal_score=legal_score,
        legal_max=30,
        quality_score=quality_score,
        quality_max=50,
        standards=results,
        red_flags=all_flags,
        risk_level=risk_level,
    )
