"""
Evidence Trail — tracks reasoning chain across all pipeline stages.
Each recommendation carries its provenance so results are explainable.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class EvidenceNode:
    stage: str          # "identification", "internal_check", "substitution", "ranking", "consolidation"
    claim: str          # human-readable assertion
    source: str         # where the evidence came from
    confidence: float   # 0.0 – 1.0
    data_ref: dict = field(default_factory=dict)  # pointers to underlying data


class EvidenceTrail:
    """Accumulates evidence nodes across pipeline stages."""

    def __init__(self):
        self._nodes: list[EvidenceNode] = []

    def add(
        self,
        stage: str,
        claim: str,
        source: str,
        confidence: float = 1.0,
        data_ref: Optional[dict] = None,
    ) -> None:
        self._nodes.append(
            EvidenceNode(
                stage=stage,
                claim=claim,
                source=source,
                confidence=min(max(confidence, 0.0), 1.0),
                data_ref=data_ref or {},
            )
        )

    def to_dict(self) -> list[dict]:
        return [asdict(n) for n in self._nodes]

    def summary(self) -> str:
        lines = []
        for n in self._nodes:
            conf = f"{n.confidence:.0%}"
            lines.append(f"[{n.stage}] {n.claim} (source: {n.source}, confidence: {conf})")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self):
        return iter(self._nodes)
