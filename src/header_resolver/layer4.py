"""Layer 4 — LLM reasoning over the headers Layers 0-2 could not settle.

Only sees the review queue, which on a representative file is a small fraction
of the columns. Three things make it safe enough to act on:

1. **Structured output.** The response is schema-constrained JSON, so a
   malformed mapping cannot occur.
2. **Abstention is a first-class answer.** `target: null` is explicitly
   allowed and encouraged. A wrong auto-applied mapping costs far more than an
   extra row in the review queue.
3. **The full schema is supplied; targets are validated on return.** Early
   versions shortlisted candidates by string similarity, which is
   self-defeating here: "Premium Amt" has no textual overlap with `Amount`, so
   ranking pruned the correct answer out of the list and forced an abstention.
   The whole schema now goes in the cacheable system prompt, sample-value type
   inference narrows it, and any target outside the schema is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fuzzy import rank_candidates
from .guards import GuardList
from .llm import LLMClient, LLMError
from .models import Layer, Mapping, ReviewReason
from .schema import Schema
from .typing_hints import compatible, infer_type

# Bounded so one bad file cannot produce an enormous prompt.
DEFAULT_BATCH_SIZE = 12
DEFAULT_CANDIDATES = 6
DEFAULT_SAMPLE_VALUES = 5

SYSTEM_PROMPT = """\
You map column headers from partner-supplied insurance CSV files onto a fixed \
canonical schema.

You only see headers that deterministic matching could not resolve, so expect \
genuine ambiguity. Your job is accuracy, not coverage.

Rules:

1. Choose a target from the canonical schema listed below. Never invent a \
field name that is not in that list.
2. If no field is clearly right, set "target": null. Abstaining is the \
correct answer for a genuinely ambiguous header — a wrong mapping is far more \
costly than an unresolved one, because it silently corrupts policy records.
3. Several distinct people share one row: the proposer (unsuffixed fields), \
the insured member (fields suffixed "_Plan"), the nominee (prefixed \
"Nominee"), and the guardian (prefixed "Guardian"). Attributes such as name \
and date of birth exist separately for each. Never assume an unqualified \
header belongs to the proposer — if the file gives no evidence of which \
person is meant, abstain.
4. Sample values are stronger evidence than the header text. A column named \
"col_17" holding ISO dates is a date field; a header whose values contradict \
its name should follow the values.
5. Watch for near-identical field pairs with opposite meanings (start vs end \
date, insured vs nominee, amount vs frequency). When two candidates differ \
only by such a distinction and the evidence does not separate them, abstain.
6. Set "confidence" to your genuine belief, 0.0-1.0. Do not inflate it. Only \
exceed 0.9 when the evidence is unambiguous.

Give brief, concrete reasoning that cites the evidence you used."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {
                        "type": ["string", "null"],
                        "description": "Canonical field name, or null to abstain",
                    },
                    "confidence": {"type": "number"},
                    "entity": {
                        "type": ["string", "null"],
                        "description": "proposer | insured | nominee | guardian, if applicable",
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["source", "target", "confidence", "entity", "reasoning"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mappings"],
    "additionalProperties": False,
}


@dataclass
class Layer4Config:
    batch_size: int = DEFAULT_BATCH_SIZE
    candidates_per_header: int = DEFAULT_CANDIDATES
    sample_values: int = DEFAULT_SAMPLE_VALUES
    accept_threshold: float = 0.85   # below this, keep in review rather than auto-apply
    fail_open: bool = True           # on LLM error, leave unresolved rather than raise


@dataclass
class Layer4Result:
    resolved: int = 0
    abstained: int = 0
    rejected: int = 0        # model named a target outside the schema
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resolved": self.resolved,
            "abstained": self.abstained,
            "rejected": self.rejected,
            "errors": self.errors,
        }


class Layer4Resolver:
    """Applies LLM reasoning to unresolved / low-confidence mappings."""

    def __init__(
        self,
        schema: Schema,
        client: LLMClient,
        guards: GuardList | None = None,
        config: Layer4Config | None = None,
    ) -> None:
        self.schema = schema
        self.client = client
        self.guards = guards
        self.cfg = config or Layer4Config()

    # ------------------------------------------------------------------

    def resolve(
        self,
        mappings: list[Mapping],
        samples: dict[str, list[str]] | None = None,
    ) -> Layer4Result:
        """Resolve in place. Only touches mappings flagged for review."""
        pending = [m for m in mappings if m.needs_review or m.target is None]
        result = Layer4Result()
        if not pending:
            return result

        by_source = {m.source: m for m in mappings}
        valid_targets = {f.key for f in self.schema.fields}

        for batch in self._batches(pending):
            try:
                answer = self.client.complete_json(
                    self._system_prompt(),
                    self._build_prompt(batch, samples or {}),
                    RESPONSE_SCHEMA,
                )
            except LLMError as exc:
                result.errors.append(str(exc))
                if not self.cfg.fail_open:
                    raise
                continue

            for item in answer.get("mappings", []):
                self._apply(item, by_source, valid_targets, result)

        return result

    def _system_prompt(self) -> str:
        """Rules plus the full schema. Stable across calls, so it caches."""
        return f"{SYSTEM_PROMPT}\n\n{self.schema_block()}"

    # ------------------------------------------------------------------

    def _apply(self, item, by_source, valid_targets, result: Layer4Result) -> None:
        m = by_source.get(item.get("source"))
        if m is None:
            return

        target = item.get("target")
        confidence = float(item.get("confidence") or 0.0)
        reasoning = (item.get("reasoning") or "").strip()

        # Abstention — the model declined, which is a valid outcome.
        if not target:
            result.abstained += 1
            m.note = f"LLM abstained: {reasoning}" if reasoning else "LLM abstained"
            return

        # Hallucination guard: the target must exist in the schema.
        if target not in valid_targets:
            result.rejected += 1
            m.note = f"LLM proposed unknown field {target!r}; ignored"
            return

        m.target = target
        m.confidence = confidence
        m.resolved_by = Layer.LLM
        m.entity = item.get("entity") or m.entity
        m.note = reasoning or None

        if confidence >= self.cfg.accept_threshold:
            # Clear the Layer 0-2 review flags this call has now answered.
            m.needs_review = False
            m.review_reasons = []
            result.resolved += 1
        else:
            m.needs_review = True
            m.review_reasons = [ReviewReason.LOW_CONFIDENCE]
            result.abstained += 1

    # ------------------------------------------------------------------

    def _batches(self, pending: list[Mapping]):
        size = max(1, self.cfg.batch_size)
        for i in range(0, len(pending), size):
            yield pending[i : i + size]

    def schema_block(self) -> str:
        """The full canonical schema, grouped by entity.

        Sent in the system prompt rather than as a per-header candidate list.
        Ranking candidates by string similarity is self-defeating for this
        layer: a header like "Premium Amt" has no textual overlap with
        `Amount`, so similarity ranking prunes the correct answer out of the
        list entirely and the model is forced to abstain.

        58 fields is roughly 900 tokens and identical on every call, so it sits
        in the cacheable prefix and costs ~0.1x after the first request.
        """
        lines = ["The canonical schema has these fields:", ""]

        shared = [f for f in self.schema.fields if not f.entity]
        if shared:
            lines.append("Shared fields (not tied to a person):")
            for f in shared:
                lines.append(f"  - {f.key}  (type={f.type}{'; required' if f.required else ''})")
            lines.append("")

        for name, group in self.schema.entities.items():
            fields = self.schema.fields_for_entity(name)
            if not fields:
                continue
            lines.append(f"{group.label} — fields belonging to the {name}:")
            for f in fields:
                lines.append(f"  - {f.key}  (type={f.type}{'; required' if f.required else ''})")
            lines.append("")

        if self.guards:
            cross = [p for p in self.guards.pairs if p.cross_entity]
            if cross:
                lines.append(
                    "These pairs look nearly identical but belong to DIFFERENT "
                    "people. Confusing them means the wrong person is insured:"
                )
                for p in sorted(cross, key=lambda x: x.score, reverse=True)[:12]:
                    lines.append(f"  - {p.a}  vs  {p.b}")
                lines.append("")

        return "\n".join(lines)

    def _build_prompt(
        self, batch: list[Mapping], samples: dict[str, list[str]]
    ) -> str:
        lines: list[str] = [
            "Resolve each header below. Reply with one entry per header.",
            "",
        ]

        for m in batch:
            lines.append(f'header: "{m.source}"')

            vals = samples.get(m.source) or []
            inferred = infer_type(vals)
            if vals:
                shown = ", ".join(repr(v) for v in vals[: self.cfg.sample_values])
                lines.append(f"  sample values: {shown}")
            if inferred != "unknown":
                lines.append(
                    f"  values look like: {inferred} "
                    f"(so the target should be a {inferred} field)"
                )

            if m.review_reasons:
                why = ", ".join(r.value for r in m.review_reasons)
                lines.append(f"  deterministic matching flagged: {why}")

            plausible = self._plausible_fields(m, inferred)
            if plausible:
                lines.append(
                    f"  fields matching that type: {', '.join(plausible)}"
                )

            closest = self._closest_by_text(m)
            if closest:
                lines.append(f"  closest by spelling: {closest}")
                lines.append(
                    "  (spelling closeness is a weak hint only — you may pick "
                    "any field from the schema)"
                )
            lines.append("")

        return "\n".join(lines)

    def _plausible_fields(self, m: Mapping, inferred: str) -> list[str]:
        """Schema fields whose type accepts this column's inferred type.

        This is what actually surfaces the right answer for a header with no
        textual overlap: ISO-date samples reduce 58 fields to the 8 date
        fields, one of which is correct.
        """
        if inferred == "unknown":
            return []
        return [f.key for f in self.schema.fields if compatible(inferred, f.type)]

    def _closest_by_text(self, m: Mapping) -> str:
        """Fuzzy top-K, offered as a hint rather than a constraint."""
        ranked = rank_candidates(
            m.source, self.schema, limit=self.cfg.candidates_per_header
        )
        return ", ".join(
            f"{c.field.key} ({c.score:.0f})" for c in ranked if c.score >= 50
        )
