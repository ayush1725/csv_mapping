"""Auto-derived confusable-pair guard list.

Rather than hand-maintaining a blocklist, we compute it: any two canonical
fields that score above `threshold` against *each other* are registered as
confusable. A fuzzy match landing on either side of such a pair is escalated
instead of auto-applied.

On the policy_v2 schema this yields ~36 pairs, including the ones that would
otherwise cause the worst failures:

    Address Line 1     vs Address Line 2      97.1
    Transaction Id     vs Transaction Date    94.8
    First Name         vs First Name_Plan     93.3   (different person!)
    Nominee First Name vs Nominee Last Name   91.6

Because it is derived, it regenerates automatically whenever the schema
changes — no maintenance burden and no drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .fuzzy import score_pair, tokens_contained
from .models import CanonicalField
from .normalize import normalize
from .schema import Schema

DEFAULT_THRESHOLD = 80.0
# Fields longer than this are declarations/questions, not names; pairwise
# similarity against them is meaningless.
_MAX_LEN_FOR_PAIRING = 60


@dataclass(frozen=True)
class ConfusablePair:
    a: str
    b: str
    score: float
    metric: str
    cross_entity: bool

    def other(self, key: str) -> str | None:
        if key == self.a:
            return self.b
        if key == self.b:
            return self.a
        return None


class GuardList:
    """Index of confusable canonical-field pairs for one schema."""

    def __init__(self, pairs: list[ConfusablePair]) -> None:
        self.pairs = pairs
        self._index: dict[str, list[ConfusablePair]] = {}
        for p in pairs:
            self._index.setdefault(p.a, []).append(p)
            self._index.setdefault(p.b, []).append(p)

    def __len__(self) -> int:
        return len(self.pairs)

    def is_confusable(self, key: str) -> bool:
        return key in self._index

    def partners(self, key: str) -> list[ConfusablePair]:
        return self._index.get(key, [])

    def cross_entity_partners(self, key: str) -> list[ConfusablePair]:
        """Confusable partners belonging to a *different* entity.

        These are the high-severity ones — confusing Address Line 1 with
        Line 2 is untidy; confusing the proposer with the nominee means the
        wrong person is insured.
        """
        return [p for p in self._index.get(key, []) if p.cross_entity]

    def top(self, n: int = 10) -> list[ConfusablePair]:
        return sorted(self.pairs, key=lambda p: p.score, reverse=True)[:n]


def build_guard_list(
    schema: Schema, threshold: float = DEFAULT_THRESHOLD
) -> GuardList:
    """Compute all confusable pairs within a schema."""
    candidates: list[CanonicalField] = [
        f for f in schema.fields if len(f.name) <= _MAX_LEN_FOR_PAIRING
    ]

    pairs: list[ConfusablePair] = []
    for a, b in combinations(candidates, 2):
        na, nb = normalize(a.name), normalize(b.name)
        score, metric = score_pair(na, nb)

        # Token containment catches entity suffixing ("title" c "title_plan")
        # which the similarity metrics deliberately no longer inflate.
        contained = tokens_contained(na, nb)
        if score < threshold and not contained:
            continue

        pairs.append(
            ConfusablePair(
                a=a.key,
                b=b.key,
                score=score,
                metric=metric if score >= threshold else "containment",
                cross_entity=(
                    a.entity is not None
                    and b.entity is not None
                    and a.entity != b.entity
                ),
            )
        )

    return GuardList(pairs)
