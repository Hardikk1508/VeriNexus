from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- internal


@dataclass
class Chunk:
    """One retrievable unit of evidence."""

    chunk_id: str
    text: str
    source_id: str
    title: str
    url: str
    domain: str
    kind: str = "web"  # web | upload
    published: Optional[str] = None
    offset: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "kind": self.kind,
            "published": self.published,
            "offset": self.offset,
        }


@dataclass
class Source:
    source_id: str
    title: str
    url: str
    domain: str
    kind: str = "web"
    authority: float = 0.5
    chunk_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "kind": self.kind,
            "authority": round(self.authority, 3),
            "chunk_count": self.chunk_count,
        }


@dataclass
class EvidenceVerdict:
    """Result of NLI(evidence -> claim) for a single evidence chunk."""

    chunk_id: str
    source_id: str
    domain: str
    snippet: str
    authority: float
    p_entail: float
    p_contradict: float
    p_neutral: float
    rationale: str = ""

    @property
    def label(self) -> str:
        best = max(
            ("entail", self.p_entail),
            ("contradict", self.p_contradict),
            ("neutral", self.p_neutral),
            key=lambda x: x[1],
        )
        return best[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "domain": self.domain,
            "snippet": self.snippet,
            "authority": round(self.authority, 3),
            "label": self.label,
            "p_entail": round(self.p_entail, 3),
            "p_contradict": round(self.p_contradict, 3),
            "rationale": self.rationale,
        }


@dataclass
class Claim:
    claim_id: str
    text: str
    centrality: str = "core"  # core | supporting
    cited: List[str] = field(default_factory=list)

    # filled by the verifier
    verdicts: List[EvidenceVerdict] = field(default_factory=list)
    support: float = 0.0
    agreement: float = 0.0
    authority: float = 0.0
    contradiction: float = 0.0
    confidence: float = 0.0
    band: str = "UNSUPPORTED"
    conflicted: bool = False
    action: str = "keep"  # keep | hedge | drop

    @property
    def weight(self) -> float:
        return 1.0 if self.centrality == "core" else 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "centrality": self.centrality,
            "weight": self.weight,
            "signals": {
                "support": round(self.support, 3),
                "agreement": round(self.agreement, 3),
                "authority": round(self.authority, 3),
                "contradiction": round(self.contradiction, 3),
            },
            "confidence": round(self.confidence, 3),
            "band": self.band,
            "conflicted": self.conflicted,
            "action": self.action,
            "evidence": [v.to_dict() for v in self.verdicts],
        }


# ---------------------------------------------------------------- API


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=8, max_length=800)
    session_id: Optional[str] = None
    upload_ids: List[str] = Field(default_factory=list)
    max_repair_rounds: Optional[int] = Field(default=None, ge=0, le=3)


class AuditEvent(BaseModel):
    step: str
    label: str
    started_at: float
    duration_ms: int
    detail: Dict[str, Any] = Field(default_factory=dict)


class ResearchResponse(BaseModel):
    session_id: str
    query: str
    answer: str
    confidence: float
    band: str
    needs_human_review: bool
    claims: List[Dict[str, Any]]
    sources: List[Dict[str, Any]]
    flags: List[str]
    audit: List[Dict[str, Any]]
    stats: Dict[str, Any]
