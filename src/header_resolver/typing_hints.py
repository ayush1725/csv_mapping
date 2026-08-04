"""Infer a column's type from its sample values.

The cheapest strong signal available. A column named `col_17` is opaque as
text, but values like `2020-01-15` make it unambiguously a date — which
collapses 58 candidate fields down to the 8 date fields.

Deliberately conservative: `unknown` is returned unless the evidence is clear,
because a wrong type hint prunes the correct answer out of the candidate list
entirely.
"""

from __future__ import annotations

import re

_DATE_PATTERNS = [
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"),          # 2020-01-15
    re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$"),          # 15/01/2020
    re.compile(r"^\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4}$"),  # 15-Jan-2020
]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?\d[\d\s\-()]{7,17}$")
_DECIMAL = re.compile(r"^-?\d{1,3}(,\d{2,3})*(\.\d+)?$|^-?\d+(\.\d+)?$")
_INTEGER = re.compile(r"^-?\d+$")
_BOOLEAN = {
    "y", "n", "yes", "no", "true", "false", "t", "f", "1", "0",
}

# Fraction of non-empty samples that must agree before a type is claimed.
_AGREEMENT = 0.8
_MIN_SAMPLES = 2

# Missing-value markers, which are not evidence of anything. Real partner
# files are full of these; counting them as values drags a clean column below
# the agreement threshold and loses the type hint entirely.
_NULL_MARKERS = {
    "n/a", "na", "#n/a", "null", "nil", "none", "-", "--", ".", "?",
    "not available", "not applicable", "blank", "xxx",
}


def infer_type(values: list[str] | None) -> str:
    """Return one of: date, email, phone, decimal, integer, boolean, unknown."""
    if not values:
        return "unknown"

    clean = [
        str(v).strip()
        for v in values
        if v is not None
        and str(v).strip()
        and str(v).strip().lower() not in _NULL_MARKERS
    ]
    if len(clean) < _MIN_SAMPLES:
        return "unknown"

    def frac(pred) -> float:
        return sum(1 for v in clean if pred(v)) / len(clean)

    # Order matters: booleans and integers overlap ("1"), dates and decimals
    # do not. Most specific first.
    if frac(lambda v: any(p.match(v) for p in _DATE_PATTERNS)) >= _AGREEMENT:
        return "date"
    if frac(lambda v: bool(_EMAIL.match(v))) >= _AGREEMENT:
        return "email"
    if frac(lambda v: v.lower() in _BOOLEAN) >= _AGREEMENT:
        return "boolean"
    if frac(lambda v: bool(_PHONE.match(v))) >= _AGREEMENT:
        return "phone"
    if frac(lambda v: bool(_DECIMAL.match(v)) and not _INTEGER.match(v)) >= _AGREEMENT:
        return "decimal"
    if frac(lambda v: bool(_INTEGER.match(v))) >= _AGREEMENT:
        return "integer"

    return "unknown"


# Canonical types an inferred type is allowed to match. Numeric kinds are
# deliberately permissive in one direction: an integer-looking sample may
# legitimately belong to a decimal field ("1250" for an amount).
_COMPATIBLE: dict[str, set[str]] = {
    "date": {"date"},
    "email": {"email", "string"},
    "phone": {"phone", "string"},
    "boolean": {"boolean", "enum"},
    "decimal": {"decimal"},
    "integer": {"integer", "decimal", "string"},
}


def compatible(inferred: str, canonical_type: str) -> bool:
    """Whether a canonical field's type can accept a column of this type."""
    if inferred == "unknown":
        return True
    return canonical_type in _COMPATIBLE.get(inferred, set())
