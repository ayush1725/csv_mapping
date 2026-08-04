"""Layer 1 — the alias dictionary and its learning loop.

An exact lookup of normalized partner header -> canonical field key. Every
confirmed mapping (human-reviewed or high-confidence auto-applied) is written
back here, so the layer that costs nothing grows to cover more of the traffic
over time and the LLM is needed less.

Scoped by (tenant, schema_id) — `amount` means premium_amount in the policy
schema and claim_amount in claims. One flat dictionary and the two poison each
other, and retrofitting namespaces onto a populated store is painful.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import normalize


@dataclass(frozen=True)
class AliasKey:
    tenant: str
    schema_id: str

    def as_str(self) -> str:
        return f"{self.tenant}::{self.schema_id}"


@dataclass
class AliasEntry:
    target: str
    hits: int = 0
    confirmed_by: str = "seed"   # seed | human | auto


@dataclass
class AliasStore:
    """In-memory alias dictionary with optional JSON persistence.

    Deliberately a plain dict rather than a database: the whole point of the
    tiered design is that this layer is trivially fast. Swap the persistence
    for the production datastore without touching the lookup path.
    """

    _data: dict[str, dict[str, AliasEntry]] = field(default_factory=lambda: defaultdict(dict))

    # ---- lookup --------------------------------------------------------

    def get(self, key: AliasKey, header: str) -> AliasEntry | None:
        return self._data.get(key.as_str(), {}).get(normalize(header))

    def __len__(self) -> int:
        return sum(len(v) for v in self._data.values())

    # ---- learning loop -------------------------------------------------

    def record(
        self,
        key: AliasKey,
        header: str,
        target: str,
        confirmed_by: str = "human",
    ) -> AliasEntry:
        """Register a confirmed mapping so future files resolve it for free."""
        bucket = self._data.setdefault(key.as_str(), {})
        norm = normalize(header)
        existing = bucket.get(norm)

        if existing and existing.target == target:
            existing.hits += 1
            # A human confirmation always outranks an automatic one.
            if confirmed_by == "human":
                existing.confirmed_by = "human"
            return existing

        entry = AliasEntry(target=target, hits=1, confirmed_by=confirmed_by)
        bucket[norm] = entry
        return entry

    def seed(self, key: AliasKey, pairs: dict[str, str]) -> None:
        for header, target in pairs.items():
            self.record(key, header, target, confirmed_by="seed")

    # ---- persistence ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            scope: {
                h: {"target": e.target, "hits": e.hits, "confirmed_by": e.confirmed_by}
                for h, e in entries.items()
            }
            for scope, entries in self._data.items()
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "AliasStore":
        store = cls()
        p = Path(path)
        if not p.exists():
            return store
        raw = json.loads(p.read_text(encoding="utf-8"))
        for scope, entries in raw.items():
            store._data[scope] = {
                h: AliasEntry(
                    target=e["target"],
                    hits=e.get("hits", 0),
                    confirmed_by=e.get("confirmed_by", "seed"),
                )
                for h, e in entries.items()
            }
        return store
