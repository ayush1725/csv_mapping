"""Two-stage resolution: decide *whose* column this is, then *which* attribute.

The policy schema packs four people into one row (proposer, insured, nominee,
guardian). 17 of 58 fields are person-attributes. Treating them as flat fields
produces a systematic — not random — error: the unsuffixed variant is always
the shortest string, so a bare `first_name` scores highest against the
*proposer* every single time, even in a file that means the insured.

Three signals fix this, in descending order of reliability:

1. Explicit marker    "Nominee First Name", "First Name_Plan"  -> certain
2. Sibling exclusion  file has both `first_name` and `nominee_first_name`,
                      so the bare one cannot be the nominee              -> strong
3. Block adjacency    columns 38-42 resolved to nominee, so 43 probably is too
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalize import normalize
from .schema import Schema

# How far to look left/right when inferring entity from neighbouring columns.
_ADJACENCY_WINDOW = 2


@dataclass
class EntityVerdict:
    entity: str | None
    confidence: float          # 0..1
    signal: str                # explicit | sibling_exclusion | adjacency | default | none
    ambiguous: bool = False


def detect_explicit(header: str, schema: Schema) -> str | None:
    """Entity from an unambiguous prefix or suffix in the header itself."""
    h = normalize(header)
    for name, group in schema.entities.items():
        if group.prefix and h.startswith(normalize(group.prefix) + "_"):
            return name
        if group.suffix and h.endswith(normalize(group.suffix)):
            return name
    return None


def _attribute_of(header: str, schema: Schema) -> str | None:
    """Which person-attribute a header looks like, ignoring entity markers.

    Strips any entity prefix/suffix first so "Nominee First Name" and
    "First Name_Plan" both reduce to `first_name`.
    """
    h = normalize(header)
    for group in schema.entities.values():
        if group.prefix:
            p = normalize(group.prefix) + "_"
            if h.startswith(p):
                h = h[len(p):]
        if group.suffix:
            s = normalize(group.suffix)
            if h.endswith(s):
                h = h[: -len(s)].strip("_")

    # Compare normalized-to-normalized. The schema declares `dob` while Layer 0
    # expands a "DOB" header to `date_of_birth`; comparing raw strings silently
    # drops every abbreviated person-attribute out of entity resolution.
    known = {
        normalize(f.attribute): f.attribute
        for f in schema.fields
        if f.attribute
    }
    return known.get(h)


def resolve_entities(
    headers: list[str], schema: Schema
) -> dict[str, EntityVerdict]:
    """Assign an entity to every header that looks like a person-attribute.

    Returns a verdict per header; headers that aren't person-attributes get
    `entity=None` with signal "none" and should be resolved normally.
    """
    default_entity = schema.default_entity
    verdicts: dict[str, EntityVerdict] = {}

    # --- Pass 1: explicit markers ---------------------------------------
    attributes: dict[str, str | None] = {}
    for h in headers:
        attr = _attribute_of(h, schema)
        attributes[h] = attr
        explicit = detect_explicit(h, schema)
        if explicit:
            verdicts[h] = EntityVerdict(explicit, 1.0, "explicit")
        elif attr is None:
            verdicts[h] = EntityVerdict(None, 1.0, "none")

    # Which (entity, attribute) slots are already claimed explicitly?
    claimed: dict[str, set[str]] = {}
    for h, v in verdicts.items():
        if v.signal == "explicit" and attributes.get(h):
            claimed.setdefault(attributes[h], set()).add(v.entity)

    # --- Pass 2: bare person-attributes ---------------------------------
    for idx, h in enumerate(headers):
        if h in verdicts:
            continue

        attr = attributes[h]
        taken = claimed.get(attr, set())
        possible = [
            name
            for name, f in _entities_supporting(schema, attr).items()
            if name not in taken
        ]

        if len(possible) == 1:
            # Sibling exclusion: every other entity's slot is explicitly filled,
            # so this bare header can only belong to the one that's left.
            verdicts[h] = EntityVerdict(possible[0], 0.95, "sibling_exclusion")
            continue

        neighbour = _entity_from_neighbours(headers, idx, verdicts)
        if neighbour and neighbour in possible:
            verdicts[h] = EntityVerdict(neighbour, 0.75, "adjacency")
            continue

        if default_entity and default_entity in possible:
            # Fall back to the default entity, but mark it ambiguous when other
            # entities are still unclaimed — this is exactly the case where
            # fuzzy alone would silently guess "proposer".
            verdicts[h] = EntityVerdict(
                default_entity,
                0.55 if len(possible) > 1 else 0.9,
                "default",
                ambiguous=len(possible) > 1,
            )
        else:
            verdicts[h] = EntityVerdict(None, 0.0, "none", ambiguous=True)

    return verdicts


def _entities_supporting(schema: Schema, attribute: str | None) -> dict[str, object]:
    """Entities that actually have a field for this attribute."""
    if not attribute:
        return {}
    return {
        f.entity: f
        for f in schema.fields
        if f.attribute == attribute and f.entity is not None
    }


def _entity_from_neighbours(
    headers: list[str], idx: int, verdicts: dict[str, EntityVerdict]
) -> str | None:
    """Infer entity from nearby already-decided columns.

    Person blocks arrive contiguously in practice — the nominee's title, name
    and DOB sit together — so a decided neighbour is a real signal.
    """
    lo = max(0, idx - _ADJACENCY_WINDOW)
    hi = min(len(headers), idx + _ADJACENCY_WINDOW + 1)

    seen: list[str] = []
    for j in range(lo, hi):
        if j == idx:
            continue
        v = verdicts.get(headers[j])
        if v and v.entity and v.signal in ("explicit", "sibling_exclusion"):
            seen.append(v.entity)

    if not seen:
        return None
    # Only trust an unambiguous neighbourhood.
    return seen[0] if len(set(seen)) == 1 else None
