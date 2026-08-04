"""Behavioural tests for the Layer 0-2 pipeline.

These lock in the properties that matter for correctness, not exact scores —
scores will shift as normalization improves, but "a nominee column must never
map to a proposer field" must hold regardless.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from header_resolver import Resolver, Schema, build_guard_list  # noqa: E402
from header_resolver.entities import resolve_entities  # noqa: E402
from header_resolver.fuzzy import tokens_contained  # noqa: E402
from header_resolver.models import Layer, ReviewReason  # noqa: E402
from header_resolver.normalize import normalize  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return Schema.load_builtin("policy_v2")


@pytest.fixture(scope="module")
def resolver(schema):
    return Resolver(schema)


# --- Layer 0: normalization -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Guardian FirstName", "guardian_first_name"),   # camelCase split
        ("Annual_Income", "annual_income"),              # already underscored
        ("Loan A/c No", "loan_account_number"),          # abbrev expansion
        ("No. of Lives", "number_of_lives"),
        ("DOB", "date_of_birth"),
        ("Mobile No", "mobile_number"),
    ],
)
def test_normalization(raw, expected):
    assert normalize(raw) == expected


def test_normalization_unifies_schema_inconsistency():
    """The canonical schema mixes conventions; Layer 0 must flatten them."""
    assert normalize("Guardian FirstName") == normalize("Guardian First Name")


# --- Guard list -------------------------------------------------------------


def test_guard_list_catches_entity_suffix_pairs(schema):
    guards = build_guard_list(schema)
    pairs = {(p.a, p.b) for p in guards.pairs}
    for a, b in [
        ("First Name", "First Name_Plan"),
        ("Last Name", "Last Name_Plan"),
        ("DOB", "DOB_Plan"),
        ("Customer ID", "Customer ID_Plan"),
    ]:
        assert (a, b) in pairs or (b, a) in pairs, f"{a} ~ {b} not guarded"


def test_guard_list_flags_cross_entity(schema):
    guards = build_guard_list(schema)
    cross = guards.cross_entity_partners("First Name")
    assert cross, "First Name must be cross-entity confusable"
    assert any(p.other("First Name") == "First Name_Plan" for p in cross)


def test_guard_list_excludes_unrelated_fields(schema):
    """Semantically unrelated fields must not be guarded.

    Dropping partial_ratio removed a large class of these (it scored
    'Branch Code' ~ 'Pin Code' and similar at 100 on any shared token).
    """
    guards = build_guard_list(schema)
    pairs = {frozenset((p.a, p.b)) for p in guards.pairs}
    for a, b in [
        ("Sum Insured", "Branch Name"),
        ("PAN", "City"),
        ("Occupation", "Tenure"),
        ("Email Id", "Loan Type"),
    ]:
        assert frozenset((a, b)) not in pairs, f"{a} ~ {b} should not be guarded"


def test_guard_list_stays_small(schema):
    """A guard list that flags everything is the same as no guard list.

    Known artifact: abbreviation expansion turns 'RM Code' into
    'relationship_manager_code', which legitimately scores 86 against
    'Relation'. Over-guarding costs review time but is never unsafe, so it is
    accepted rather than special-cased.
    """
    guards = build_guard_list(schema)
    total_pairs = len(schema.fields) * (len(schema.fields) - 1) / 2
    assert len(guards) < total_pairs * 0.05


def test_containment_detection():
    assert tokens_contained("title", "title_plan")
    assert tokens_contained("nominee_dob", "dob") is True or tokens_contained("dob", "nominee_dob")
    assert not tokens_contained("first_name", "last_name")
    assert not tokens_contained("dob", "dob")


# --- Entity resolution ------------------------------------------------------


def test_explicit_entity_markers(schema):
    v = resolve_entities(
        ["Nominee First Name", "First Name_Plan", "Guardian DOB"], schema
    )
    assert v["Nominee First Name"].entity == "nominee"
    assert v["First Name_Plan"].entity == "insured"
    assert v["Guardian DOB"].entity == "guardian"
    assert all(x.signal == "explicit" for x in v.values())


def test_sibling_exclusion(schema):
    """A bare header is deduced, not guessed, when siblings claim the rest."""
    headers = ["First Name", "First Name_Plan", "Nominee First Name", "Guardian FirstName"]
    v = resolve_entities(headers, schema)
    assert v["First Name"].entity == "proposer"
    assert v["First Name"].signal == "sibling_exclusion"
    assert not v["First Name"].ambiguous


def test_bare_header_alone_is_ambiguous(schema):
    """With no siblings, a bare `first_name` is a guess and must say so.

    This is the systematic-bias case: fuzzy alone always picks the proposer.
    """
    v = resolve_entities(["First Name"], schema)
    assert v["First Name"].ambiguous
    assert v["First Name"].signal == "default"
    assert v["First Name"].confidence < 0.7


def test_non_person_headers_get_no_entity(schema):
    v = resolve_entities(["Branch Code", "Sum Insured"], schema)
    assert v["Branch Code"].entity is None
    assert v["Sum Insured"].signal == "none"


@pytest.mark.parametrize("header", ["First Name", "DOB", "Title", "Customer ID"])
def test_abbreviated_attributes_still_enter_entity_resolution(header, schema):
    """Regression: Layer 0 expands "DOB" to `date_of_birth` while the schema
    declares `dob`. Comparing raw strings dropped every abbreviated
    person-attribute out of entity resolution, so a bare DOB auto-applied to
    the proposer without ever being checked.
    """
    v = resolve_entities([header], schema)
    assert v[header].entity is not None, f"{header} skipped entity resolution"
    assert v[header].ambiguous


# --- End-to-end resolution --------------------------------------------------


def test_abbreviation_expansion_resolves_exactly(resolver):
    res = resolver.resolve(["Loan Account Number"])
    m = res.mappings[0]
    assert m.target == "Loan A/c No"
    assert m.resolved_by == Layer.ALIAS
    assert not m.needs_review


def test_typo_is_absorbed_by_fuzzy(resolver):
    res = resolver.resolve(["Applicatn Date"])
    m = res.mappings[0]
    assert m.target == "Application Date"
    assert m.resolved_by == Layer.FUZZY
    assert not m.needs_review


def test_meaningless_header_is_unresolved_not_guessed(resolver):
    res = resolver.resolve(["col_17"])
    m = res.mappings[0]
    assert m.target is None
    assert m.resolved_by == Layer.UNRESOLVED
    assert ReviewReason.NO_CANDIDATE in m.review_reasons


def test_nominee_never_maps_to_proposer_field(resolver):
    """The highest-severity failure mode: wrong person insured."""
    res = resolver.resolve(["Nominee First Name", "Nominee DOB", "Nominee Title"])
    for m in res.mappings:
        assert m.target.startswith("Nominee"), f"{m.source} leaked to {m.target}"


def test_ambiguous_person_field_is_flagged(resolver):
    """A lone bare name must not silently auto-apply to the proposer."""
    res = resolver.resolve(["First Name"])
    m = res.mappings[0]
    assert m.needs_review
    assert ReviewReason.ENTITY_AMBIGUOUS in m.review_reasons


def test_conflict_detection(resolver):
    """Two columns claiming one field: keep the best, flag the rest."""
    res = resolver.resolve(["Sum Insured", "Sum Insured "])
    assert res.conflicts
    flagged = [m for m in res.mappings if ReviewReason.DUPLICATE_TARGET in m.review_reasons]
    assert len(flagged) == 1


def test_unmapped_required_is_reported(resolver):
    res = resolver.resolve(["Branch Code"])
    assert "Member Number" in res.unmapped_required
    assert "Loan A/c No" in res.unmapped_required


def test_layer_telemetry_is_recorded(resolver):
    res = resolver.resolve(["DOB", "Applicatn Date", "col_17"])
    counts = res.layer_counts()
    assert counts.get("alias", 0) >= 1
    assert counts.get("unresolved", 0) == 1


# --- Learning loop ----------------------------------------------------------


def test_confirmation_makes_next_resolve_free(schema):
    r = Resolver(schema)
    first = r.resolve(["Cust Nm"])
    assert first.mappings[0].needs_review or first.mappings[0].target is None

    r.confirm(first, "Cust Nm", "First Name")

    second = r.resolve(["Cust Nm"])
    m = second.mappings[0]
    assert m.target == "First Name"
    assert m.resolved_by == Layer.ALIAS
    assert not m.needs_review


def test_alias_store_is_tenant_scoped(schema):
    """One tenant's learned alias must not leak into another's resolution.

    Uses a header that does not normalize onto a canonical name, so the only
    way it can resolve via ALIAS is through the learned dictionary.
    """
    r = Resolver(schema)
    header = "Sanctioned Value"
    r.confirm(r.resolve([header]), header, "Loan Amount", tenant="bank_a")

    same = r.resolve([header], tenant="bank_a")
    assert same.mappings[0].target == "Loan Amount"
    assert same.mappings[0].resolved_by == Layer.ALIAS

    other = r.resolve([header], tenant="bank_b")
    assert other.mappings[0].resolved_by != Layer.ALIAS
