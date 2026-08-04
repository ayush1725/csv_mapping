"""Canonical schema loading, indexing and entity-group metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import CanonicalField
from .normalize import basic_normalize, normalize

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass
class EntityGroup:
    """A distinct real-world entity whose attributes share one CSV row.

    In the policy schema four people coexist per row (proposer, insured,
    nominee, guardian). Attributes must be attached to the right one or the
    wrong person ends up insured.
    """

    name: str
    label: str
    prefix: str | None = None
    suffix: str | None = None
    is_default: bool = False


@dataclass
class Schema:
    """An indexed canonical schema."""

    schema_id: str
    fields: list[CanonicalField]
    entities: dict[str, EntityGroup] = field(default_factory=dict)
    description: str = ""

    # Built in __post_init__
    _by_norm: dict[str, CanonicalField] = field(default_factory=dict, repr=False)
    _by_key: dict[str, CanonicalField] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for f in self.fields:
            self._by_key[f.key] = f
            self._by_norm.setdefault(normalize(f.name), f)
            # Also index the un-expanded form, so a partner header that happens
            # to use the same abbreviation still hits the exact path.
            self._by_norm.setdefault(basic_normalize(f.name), f)

    # ---- lookups -------------------------------------------------------

    def by_key(self, key: str) -> CanonicalField | None:
        return self._by_key.get(key)

    def exact(self, normalized_header: str) -> CanonicalField | None:
        return self._by_norm.get(normalized_header)

    @property
    def required(self) -> list[CanonicalField]:
        return [f for f in self.fields if f.required]

    def entity_of(self, f: CanonicalField) -> str | None:
        return f.entity

    def fields_for_entity(self, entity: str) -> list[CanonicalField]:
        return [f for f in self.fields if f.entity == entity]

    def attribute_siblings(self, attribute: str) -> list[CanonicalField]:
        """Every entity's version of one attribute, e.g. all four `first_name`."""
        return [f for f in self.fields if f.attribute == attribute]

    @property
    def default_entity(self) -> str | None:
        for name, e in self.entities.items():
            if e.is_default:
                return name
        return None

    # ---- loading -------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict) -> "Schema":
        entities: dict[str, EntityGroup] = {}
        for name, spec in (raw.get("entity_groups") or {}).items():
            detect = spec.get("detect", {})
            entities[name] = EntityGroup(
                name=name,
                label=spec.get("label", name),
                prefix=detect.get("prefix"),
                suffix=detect.get("suffix"),
                is_default=bool(detect.get("default", False)),
            )

        fields = [
            CanonicalField(
                name=f["name"],
                type=f.get("type", "string"),
                required=bool(f.get("required", False)),
                entity=f.get("entity"),
                attribute=f.get("attribute"),
                short_name=f.get("short_name"),
            )
            for f in raw["fields"]
        ]

        return cls(
            schema_id=raw["schema_id"],
            fields=fields,
            entities=entities,
            description=raw.get("description", ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Schema":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def load_builtin(cls, schema_id: str = "policy_v2") -> "Schema":
        return cls.load(_DATA_DIR / f"{schema_id}.json")
