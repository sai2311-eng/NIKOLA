"""
Agnes — CPG Procurement Intelligence Pipeline.

7-step substitution analysis and supplier consolidation engine.
"""

from .pipeline import AgnesPipeline, AgnesRunResult
from .agent import Agnes
from .context import AgnesContext
from .candidates import CandidateGenerator, Candidate
from .constraints import ConstraintInference, ConstraintSet
from .evidence_collector import EvidenceCollector
from .scoring import FeasibilityScorer, ScoreCard
from .consolidation import ConsolidationModeler, Scenario, Recommendation
from .review import ReviewBuilder
from .actions import analyze_ingredient, analyze_barcode, analyze_bottleneck
from .gmail_sync import GmailInboxStore
from .elevenlabs_prompt import AGNES_SYSTEM_PROMPT, AGNES_FIRST_MESSAGE

__all__ = [
    "AgnesPipeline",
    "AgnesRunResult",
    "Agnes",
    "AgnesContext",
    "CandidateGenerator",
    "Candidate",
    "ConstraintInference",
    "ConstraintSet",
    "EvidenceCollector",
    "FeasibilityScorer",
    "ScoreCard",
    "ConsolidationModeler",
    "Scenario",
    "Recommendation",
    "ReviewBuilder",
    "analyze_ingredient",
    "analyze_barcode",
    "analyze_bottleneck",
    "GmailInboxStore",
    "AGNES_SYSTEM_PROMPT",
    "AGNES_FIRST_MESSAGE",
]
