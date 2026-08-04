"""Sample-value type inference.

The most valuable signal Layer 4 gets. It must be conservative: an over-eager
type claim prunes the correct field out of the candidate list entirely, which
is worse than offering no hint at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from header_resolver.typing_hints import compatible, infer_type  # noqa: E402


@pytest.mark.parametrize(
    "values,expected",
    [
        (["2020-01-15", "2019-11-03", "2021-06-22"], "date"),
        (["15/01/2020", "03/11/2019"], "date"),
        (["15-Jan-2020", "03-Feb-2019"], "date"),
        (["a@b.com", "x.y@z.co.in"], "email"),
        (["9876543210", "9123456789"], "phone"),
        (["1250.00", "980.50", "3400.75"], "decimal"),
        (["12", "45", "7"], "integer"),
        (["Y", "N", "Y"], "boolean"),
        (["Yes", "No"], "boolean"),
    ],
)
def test_inference(values, expected):
    assert infer_type(values) == expected


@pytest.mark.parametrize(
    "values",
    [
        None,
        [],
        ["only-one"],                       # below minimum sample count
        ["Mumbai", "Delhi", "Chennai"],     # free text
        ["2020-01-15", "banana", "xyz"],    # no clear majority
    ],
)
def test_unknown_when_evidence_is_weak(values):
    assert infer_type(values) == "unknown"


def test_blank_values_are_ignored():
    assert infer_type(["2020-01-15", "", "  ", "2019-11-03"]) == "date"


@pytest.mark.parametrize("marker", ["N/A", "NULL", "-", "#N/A", "nil", "Not Available"])
def test_null_markers_are_not_evidence(marker):
    """Missing-value markers must not drag a clean column below the threshold."""
    assert infer_type(["1250.00", "980.50", "3400.75", marker]) == "decimal"


def test_majority_wins_over_genuine_stray_value():
    """A real off-type value is still tolerated up to the agreement threshold."""
    assert infer_type(["2020-01-15", "2019-11-03", "2021-06-22", "2018-04-01", "junk"]) == "date"


# --- Compatibility ----------------------------------------------------------


def test_unknown_matches_everything():
    """No evidence must never prune the candidate list."""
    for t in ("date", "string", "decimal", "enum", "boolean"):
        assert compatible("unknown", t)


def test_date_only_matches_date():
    assert compatible("date", "date")
    assert not compatible("date", "string")
    assert not compatible("date", "decimal")


def test_integer_may_match_decimal():
    """'1250' is a plausible amount; being strict here loses real matches."""
    assert compatible("integer", "decimal")
    assert compatible("integer", "integer")


def test_decimal_does_not_match_integer():
    """A value with decimal places cannot belong to an integer field."""
    assert compatible("decimal", "decimal")
    assert not compatible("decimal", "integer")


def test_boolean_matches_enum():
    """Y/N declarations are modelled as either boolean or enum."""
    assert compatible("boolean", "boolean")
    assert compatible("boolean", "enum")
