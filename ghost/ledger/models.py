"""Evidence ledger data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ClaimStatus(str, Enum):
    ESTABLISHED = "Established"
    PLAUSIBLE = "Plausible"
    UNSUPPORTED = "Unsupported"


@dataclass
class EvidenceRecord:
    """A single evidence item in an investigation."""

    evidence_id: str
    kind: str
    source: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source": self.source,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class InvestigationClaim:
    """A claim that needs verification against collected evidence."""

    claim_id: str
    description: str
    status: ClaimStatus | str = ClaimStatus.PLAUSIBLE
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    explanation: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, ClaimStatus) else self.status,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "contradicting_evidence_ids": self.contradicting_evidence_ids,
            "confidence": self.confidence,
            "explanation": self.explanation,
        }


@dataclass
class ClaimRelationship:
    """Relationship between a claim and relevant evidence."""

    claim_id: str
    evidence_id: str
    relation: str = "supports"


@dataclass
class InvestigationFinding:
    """Summarized finding that is grounded in evidence."""

    finding_id: str
    title: str
    summary: str
    evidence_ids: list[str] = field(default_factory=list)
    status: ClaimStatus | str = ClaimStatus.PLAUSIBLE


ClaimVerificationStatus = ClaimStatus
