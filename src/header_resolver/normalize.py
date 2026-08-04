"""Layer 0 — normalization.

Applied to partner headers *and* canonical field names. The canonical schema
is itself inconsistently formatted ("Guardian FirstName" vs "First Name" vs
"Annual_Income"), so normalizing only one side leaves easy matches on the table.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Separators that should collapse to a single underscore.
_SEPARATORS = re.compile(r"[\s\-/\\.,()\[\]{}:;'\"]+")
# Split camelCase / PascalCase: "FirstName" -> "First Name", "SumInsured" -> "Sum Insured"
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9_]+")
_MULTI_US = re.compile(r"_{2,}")


@lru_cache(maxsize=1)
def load_abbreviations(path: str | None = None) -> dict[str, str]:
    """Load the abbreviation expansion map, dropping comment keys."""
    p = Path(path) if path else _DATA_DIR / "abbreviations.json"
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def basic_normalize(text: str) -> str:
    """Casing, separators and camelCase only — no abbreviation expansion.

    "Guardian FirstName" -> "guardian_first_name"
    "Loan A/c No"        -> "loan_a_c_no"
    "Annual_Income"      -> "annual_income"
    """
    if not text:
        return ""
    s = _CAMEL_BOUNDARY.sub(" ", text.strip())
    s = _SEPARATORS.sub("_", s)
    s = s.lower()
    s = _NON_ALNUM.sub("_", s)
    s = _MULTI_US.sub("_", s)
    return s.strip("_")


def expand_abbreviations(normalized: str, abbrevs: dict[str, str] | None = None) -> str:
    """Expand known abbreviations token-by-token.

    Only whole tokens are expanded, so "id" in "identifier" is untouched while
    a standalone "id" token becomes "identifier".
    """
    abbrevs = abbrevs if abbrevs is not None else load_abbreviations()
    tokens = [t for t in normalized.split("_") if t]

    # Two-token abbreviations first ("a" + "c" -> account, from "A/c").
    merged: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            pair = f"{tokens[i]}_{tokens[i + 1]}"
            if pair in abbrevs:
                merged.append(abbrevs[pair])
                i += 2
                continue
        merged.append(tokens[i])
        i += 1

    return "_".join(abbrevs.get(t, t) for t in merged)


def normalize(text: str, expand: bool = True) -> str:
    """Full Layer 0 normalization."""
    base = basic_normalize(text)
    return expand_abbreviations(base) if expand else base


def token_set(text: str) -> frozenset[str]:
    """Normalized token set — used for containment and overlap checks."""
    return frozenset(t for t in normalize(text).split("_") if t)
