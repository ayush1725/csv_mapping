"""Core data types for the header resolution pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Layer(str, Enum):
    """Which pipeline layer produced a mapping.

    Doubles as cost telemetry: TEMPLATE/ALIAS/FUZZY are free, EMBEDDING is
    cheap, LLM is per-call.
    """

    TEMPLATE = "template"
    ALIAS = "alias"
    FUZZY = "fuzzy"
    EMBEDDING = "embedding"
    LLM = "llm"
    UNRESOLVED = "unresolved"


class ReviewReason(str, Enum):
    """Why a mapping was routed to human review instead of auto-applied."""

    LOW_CONFIDENCE = "low_confidence"
    NARROW_MARGIN = "narrow_margin"
    CONFUSABLE_PAIR = "confusable_pair"
    ENTITY_AMBIGUOUS = "entity_ambiguous"
    TYPE_MISMATCH = "type_mismatch"
    DUPLICATE_TARGET = "duplicate_target"
    NO_CANDIDATE = "no_candidate"


@dataclass(frozen=True)
class CanonicalField:
    """One field in a canonical schema."""

    name: str
    type: str = "string"
    required: bool = False
    entity: str | None = None
    attribute: str | None = None
    short_name: str | None = None

    @property
    def key(self) -> str:
        """Stable identifier. Long declaration fields carry a short_name."""
        return self.short_name or self.name


@dataclass
class Candidate:
    """A scored canonical field considered for one partner header."""

    field: CanonicalField
    score: float
    metric: str

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Candidate {self.field.name} {self.score:.1f} via {self.metric}>"


@dataclass
class Mapping:
    """The resolution outcome for a single partner header."""

    source: str
    target: str | None
    confidence: float
    resolved_by: Layer
    needs_review: bool = False
    review_reasons: list[ReviewReason] = field(default_factory=list)
    entity: str | None = None
    runners_up: list[tuple[str, float]] = field(default_factory=list)
    note: str | None = None

    def flag(self, reason: ReviewReason, note: str | None = None) -> None:
        """Mark this mapping for human review."""
        self.needs_review = True
        if reason not in self.review_reasons:
            self.review_reasons.append(reason)
        if note:
            self.note = note if not self.note else f"{self.note}; {note}"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "confidence": round(self.confidence, 3),
            "resolved_by": self.resolved_by.value,
            "needs_review": self.needs_review,
            "review_reasons": [r.value for r in self.review_reasons],
            "entity": self.entity,
            "runners_up": [(n, round(s, 1)) for n, s in self.runners_up],
            "note": self.note,
        }


@dataclass
class ResolveResult:
    """Full outcome of resolving one partner file's headers."""

    schema_id: str
    mappings: list[Mapping]
    unmapped_required: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    template_hit: bool = False

    @property
    def auto_appliable(self) -> list[Mapping]:
        return [m for m in self.mappings if m.target and not m.needs_review]

    @property
    def review_queue(self) -> list[Mapping]:
        return [m for m in self.mappings if m.needs_review]

    def layer_counts(self) -> dict[str, int]:
        """Cost telemetry: how many headers each layer resolved."""
        counts: dict[str, int] = {}
        for m in self.mappings:
            counts[m.resolved_by.value] = counts.get(m.resolved_by.value, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "schema_id": self.schema_id,
            "template_hit": self.template_hit,
            "mappings": [m.to_dict() for m in self.mappings],
            "unmapped_required": self.unmapped_required,
            "conflicts": self.conflicts,
            "layer_counts": self.layer_counts(),
        }
