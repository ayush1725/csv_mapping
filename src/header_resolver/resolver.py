"""Pipeline orchestration.

Deliberately multi-pass, because a header cannot be resolved in isolation:
whether a bare `first_name` belongs to the proposer or the insured depends on
what *else* is in the file.

    Pass 1  entity assignment across the whole header list
    Pass 2  per-header resolution — alias, then fuzzy, restricted by entity
    Pass 3  Layer 4 (LLM) on whatever passes 1-2 could not settle
    Pass 4  global reconciliation — conflicts, missing required fields

Reconciliation runs last so it sees every layer's final answer; running it
before Layer 4 would flag conflicts the LLM then resolves.

Layer 4 is opt-in: without an `llm_client` the resolver is fully deterministic
and makes no network calls. Layer 3 (embeddings) is not implemented — headers
that survive to the review queue currently go straight to Layer 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .aliases import AliasKey, AliasStore
from .entities import resolve_entities
from .fuzzy import margin, rank_candidates
from .guards import GuardList, build_guard_list
from .models import Layer, Mapping, ResolveResult, ReviewReason
from .normalize import normalize
from .schema import Schema

if TYPE_CHECKING:  # pragma: no cover
    from .layer4 import Layer4Config
    from .llm import LLMClient


@dataclass
class Thresholds:
    """Tuning knobs. Defaults are deliberately conservative.

    `auto_apply` sits at 92 rather than 80 because fuzzy's most dangerous
    errors outscore many of its correct matches — policy_start_date vs
    policy_end_date scores 86.8.
    """

    auto_apply: float = 92.0        # score at/above which a match may auto-apply
    consider: float = 70.0          # below this, treat as no candidate at all
    min_margin: float = 8.0         # required gap between top-2 candidates
    guard_margin: float = 15.0      # wider gap demanded for confusable fields
    entity_confidence: float = 0.7  # below this, entity assignment is untrusted


class Resolver:
    """Resolves partner headers against one canonical schema."""

    def __init__(
        self,
        schema: Schema,
        aliases: AliasStore | None = None,
        thresholds: Thresholds | None = None,
        guard_list: GuardList | None = None,
        llm_client: "LLMClient | None" = None,
        layer4_config: "Layer4Config | None" = None,
    ) -> None:
        self.schema = schema
        self.aliases = aliases or AliasStore()
        self.t = thresholds or Thresholds()
        self.guards = guard_list or build_guard_list(schema)

        # Layer 4 is opt-in. Without a client the resolver stays fully
        # deterministic and makes no network calls.
        self.layer4 = None
        if llm_client is not None:
            from .layer4 import Layer4Resolver

            self.layer4 = Layer4Resolver(
                schema=schema,
                client=llm_client,
                guards=self.guards,
                config=layer4_config,
            )

    # ------------------------------------------------------------------

    def resolve(
        self,
        headers: list[str],
        tenant: str = "default",
        samples: dict[str, list[str]] | None = None,
        use_llm: bool = True,
    ) -> ResolveResult:
        alias_key = AliasKey(tenant=tenant, schema_id=self.schema.schema_id)

        # Pass 1 — entity assignment needs the whole header list.
        verdicts = resolve_entities(headers, self.schema)

        # Pass 2 — per-header resolution.
        mappings: list[Mapping] = []
        for header in headers:
            mappings.append(self._resolve_one(header, verdicts, alias_key))

        # Pass 3 — Layer 4 on whatever Layers 0-2 could not settle.
        layer4_stats = None
        if self.layer4 is not None and use_llm:
            layer4_stats = self.layer4.resolve(mappings, samples).to_dict()

        # Pass 4 — global reconciliation, after every layer has had its say.
        conflicts = self._flag_conflicts(mappings)
        unmapped = self._unmapped_required(mappings)

        return ResolveResult(
            schema_id=self.schema.schema_id,
            mappings=mappings,
            unmapped_required=unmapped,
            conflicts=conflicts,
            layer4=layer4_stats,
        )

    # ------------------------------------------------------------------

    def _resolve_one(self, header, verdicts, alias_key) -> Mapping:
        verdict = verdicts.get(header)
        entity = verdict.entity if verdict else None

        # --- Layer 1: alias dictionary (exact) --------------------------
        alias = self.aliases.get(alias_key, header)
        if alias:
            return Mapping(
                source=header,
                target=alias.target,
                confidence=1.0,
                resolved_by=Layer.ALIAS,
                entity=entity,
            )

        # --- Layer 1b: exact match on the canonical name itself ---------
        exact = self.schema.exact(normalize(header))
        if exact:
            m = Mapping(
                source=header,
                target=exact.key,
                confidence=1.0,
                resolved_by=Layer.ALIAS,
                entity=exact.entity or entity,
                note="exact canonical name",
            )
            # An exact *name* match is not an exact *entity* match. A bare
            # "First Name" matches the proposer's field perfectly while the
            # file may well mean the insured — the systematic bias this
            # pipeline exists to catch. Ambiguity survives an exact match.
            if exact.entity and verdict and verdict.ambiguous:
                m.confidence = 0.6
                m.flag(
                    ReviewReason.ENTITY_AMBIGUOUS,
                    f"exact name match but entity '{verdict.entity}' only inferred "
                    f"via {verdict.signal}",
                )
            return m

        # --- Layer 2: fuzzy, restricted by entity when known ------------
        restrict = None
        if entity and verdict and verdict.confidence >= self.t.entity_confidence:
            restrict = self.schema.fields_for_entity(entity) or None

        candidates = rank_candidates(header, self.schema, limit=5, restrict_to=restrict)
        if not candidates or candidates[0].score < self.t.consider:
            m = Mapping(
                source=header,
                target=None,
                confidence=0.0,
                resolved_by=Layer.UNRESOLVED,
                entity=entity,
                runners_up=[(c.field.key, c.score) for c in candidates[:3]],
            )
            m.flag(ReviewReason.NO_CANDIDATE, "no candidate above consider threshold")
            return m

        top = candidates[0]
        gap = margin(candidates)

        m = Mapping(
            source=header,
            target=top.field.key,
            confidence=top.score / 100.0,
            resolved_by=Layer.FUZZY,
            entity=top.field.entity or entity,
            runners_up=[(c.field.key, c.score) for c in candidates[1:4]],
            note=f"metric={top.metric}",
        )

        self._apply_guards(m, top, gap, verdict)
        return m

    # ------------------------------------------------------------------

    def _apply_guards(self, m: Mapping, top, gap: float, verdict) -> None:
        """Every reason a high-scoring fuzzy match still shouldn't auto-apply."""

        if top.score < self.t.auto_apply:
            m.flag(
                ReviewReason.LOW_CONFIDENCE,
                f"score {top.score:.1f} < {self.t.auto_apply}",
            )

        # Confusable fields demand a wider margin than ordinary ones.
        confusable = self.guards.is_confusable(top.field.key)
        required_margin = self.t.guard_margin if confusable else self.t.min_margin
        if gap < required_margin:
            m.flag(
                ReviewReason.NARROW_MARGIN,
                f"top-2 gap {gap:.1f} < {required_margin}",
            )

        # Cross-entity confusables are the wrong-person-insured failure mode.
        cross = self.guards.cross_entity_partners(top.field.key)
        if cross and gap < self.t.guard_margin:
            names = ", ".join(p.other(top.field.key) or "?" for p in cross[:3])
            m.flag(
                ReviewReason.CONFUSABLE_PAIR,
                f"confusable across entities with: {names}",
            )

        # The entity itself was a guess rather than a deduction.
        if verdict and verdict.ambiguous:
            m.flag(
                ReviewReason.ENTITY_AMBIGUOUS,
                f"entity '{verdict.entity}' inferred via {verdict.signal}",
            )

    # ------------------------------------------------------------------

    def _flag_conflicts(self, mappings: list[Mapping]) -> list[dict]:
        """Two partner columns claiming the same canonical field."""
        seen: dict[str, list[Mapping]] = {}
        for m in mappings:
            if m.target:
                seen.setdefault(m.target, []).append(m)

        conflicts = []
        for target, claimants in seen.items():
            if len(claimants) < 2:
                continue
            conflicts.append(
                {
                    "target": target,
                    "sources": [c.source for c in claimants],
                }
            )
            # Keep the strongest claim; everything else goes to review.
            claimants.sort(key=lambda m: m.confidence, reverse=True)
            for loser in claimants[1:]:
                loser.flag(
                    ReviewReason.DUPLICATE_TARGET,
                    f"'{claimants[0].source}' also claims {target}",
                )
        return conflicts

    def _unmapped_required(self, mappings: list[Mapping]) -> list[str]:
        """Required canonical fields nothing mapped to — a hard stop."""
        hit = {m.target for m in mappings if m.target and not m.needs_review}
        return [f.key for f in self.schema.required if f.key not in hit]

    # ------------------------------------------------------------------

    def confirm(
        self, result: ResolveResult, source: str, target: str, tenant: str = "default"
    ) -> None:
        """Record a human decision, feeding the learning loop."""
        self.aliases.record(
            AliasKey(tenant=tenant, schema_id=self.schema.schema_id),
            source,
            target,
            confirmed_by="human",
        )
