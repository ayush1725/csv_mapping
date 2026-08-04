"""Layer 4 tests.

All run offline against MockLLMClient. What matters here is not that the model
is smart — it's that a wrong or malicious answer cannot corrupt a mapping:
abstentions are honoured, hallucinated fields are rejected, low confidence
stays in review, and an outage degrades to the deterministic result.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from header_resolver import (  # noqa: E402
    Layer4Config,
    MockLLMClient,
    Resolver,
    Schema,
)
from header_resolver.layer4 import RESPONSE_SCHEMA, Layer4Resolver  # noqa: E402
from header_resolver.llm import LLMError  # noqa: E402
from header_resolver.models import Layer, ReviewReason  # noqa: E402


@pytest.fixture(scope="module")
def schema():
    return Schema.load_builtin("policy_v2")


# --- Layer 4 is opt-in ------------------------------------------------------


def test_no_client_means_no_llm(schema):
    """Without a client the resolver must stay fully deterministic."""
    r = Resolver(schema)
    res = r.resolve(["col_17"])
    assert r.layer4 is None
    assert res.layer4 is None
    assert res.mappings[0].resolved_by == Layer.UNRESOLVED


def test_use_llm_false_skips_layer4(schema):
    client = MockLLMClient(responses={"col_17": "Transaction Id"})
    r = Resolver(schema, llm_client=client)
    res = r.resolve(["col_17"], use_llm=False)
    assert res.layer4 is None
    assert client.calls == []


# --- Happy path -------------------------------------------------------------


def test_llm_resolves_what_fuzzy_could_not(schema):
    client = MockLLMClient(responses={"col_17": "Transaction Id"}, confidence=0.93)
    r = Resolver(schema, llm_client=client)
    res = r.resolve(["col_17"])

    m = res.mappings[0]
    assert m.target == "Transaction Id"
    assert m.resolved_by == Layer.LLM
    assert not m.needs_review
    assert res.layer4["resolved"] == 1


def test_only_review_queue_is_sent(schema):
    """Confidently-resolved headers must never reach the model."""
    client = MockLLMClient(responses={})
    r = Resolver(schema, llm_client=client)
    r.resolve(["Occupation", "Branch Code", "col_17"])

    assert len(client.calls) == 1
    prompt = client.calls[0]
    assert "col_17" in prompt
    assert "Occupation" not in prompt.split("candidates:")[0]


# --- Safety: the model must not be able to do damage ------------------------


def test_abstention_is_honoured(schema):
    """target=null must leave the header unresolved, not force a guess."""
    client = MockLLMClient(responses={})   # abstains on everything
    r = Resolver(schema, llm_client=client)
    res = r.resolve(["col_17"])

    m = res.mappings[0]
    assert m.target is None
    assert m.needs_review
    assert res.layer4["abstained"] == 1
    assert res.layer4["resolved"] == 0


def test_hallucinated_field_is_rejected(schema):
    """A target outside the schema must be discarded, not written through."""
    client = MockLLMClient(responses={"col_17": "Totally Made Up Field"})
    r = Resolver(schema, llm_client=client)
    res = r.resolve(["col_17"])

    m = res.mappings[0]
    assert m.target is None
    assert res.layer4["rejected"] == 1
    assert "unknown field" in (m.note or "")


def test_low_confidence_stays_in_review(schema):
    """Answering is not the same as being trusted."""
    client = MockLLMClient(responses={"col_17": "Transaction Id"}, confidence=0.55)
    r = Resolver(schema, llm_client=client)
    res = r.resolve(["col_17"])

    m = res.mappings[0]
    assert m.target == "Transaction Id"      # recorded as a suggestion
    assert m.needs_review                     # but not auto-applied
    assert ReviewReason.LOW_CONFIDENCE in m.review_reasons
    assert res.layer4["resolved"] == 0


def test_accept_threshold_is_configurable(schema):
    client = MockLLMClient(responses={"col_17": "Transaction Id"}, confidence=0.7)
    r = Resolver(
        schema,
        llm_client=client,
        layer4_config=Layer4Config(accept_threshold=0.6),
    )
    res = r.resolve(["col_17"])
    assert not res.mappings[0].needs_review
    assert res.layer4["resolved"] == 1


# --- Failure handling -------------------------------------------------------


class _BrokenClient:
    def complete_json(self, system, user, schema):
        raise LLMError("simulated outage")


def test_llm_outage_degrades_to_deterministic_result(schema):
    """An LLM failure must not fail the upload — it falls back to review."""
    r = Resolver(schema, llm_client=_BrokenClient())
    res = r.resolve(["col_17"])

    m = res.mappings[0]
    assert m.target is None
    assert m.needs_review
    assert res.layer4["errors"]


def test_fail_closed_raises_when_configured(schema):
    r = Resolver(
        schema,
        llm_client=_BrokenClient(),
        layer4_config=Layer4Config(fail_open=False),
    )
    with pytest.raises(LLMError):
        r.resolve(["col_17"])


# --- Prompt construction ----------------------------------------------------


def test_prompt_includes_sample_values(schema):
    client = MockLLMClient(responses={})
    r = Resolver(schema, llm_client=client)
    r.resolve(["col_17"], samples={"col_17": ["2020-01-15", "2019-11-03"]})

    prompt = client.calls[0]
    assert "sample values" in prompt
    assert "2020-01-15" in prompt


def test_prompt_warns_about_cross_entity_confusables(schema):
    """The model must be told which candidates are dangerous look-alikes."""
    client = MockLLMClient(responses={})
    r = Resolver(schema, llm_client=client)
    r.resolve(["First Name"])

    system = client.systems[0]
    assert "belong to DIFFERENT" in system
    assert "First Name_Plan" in system


def test_prompt_carries_entity_ownership(schema):
    client = MockLLMClient(responses={})
    r = Resolver(schema, llm_client=client)
    r.resolve(["Nominee Naem"])   # typo, will land in review

    system = client.systems[0]
    assert "fields belonging to the nominee" in system
    assert "fields belonging to the proposer" in system


def test_batching_splits_large_queues(schema):
    client = MockLLMClient(responses={})
    r = Resolver(
        schema,
        llm_client=client,
        layer4_config=Layer4Config(batch_size=2),
    )
    r.resolve(["col_1", "col_2", "col_3", "col_4", "col_5"])
    assert len(client.calls) == 3   # 2 + 2 + 1


# --- Response schema --------------------------------------------------------


def test_response_schema_permits_abstention():
    """Structured output must allow null, or the model cannot decline."""
    target = RESPONSE_SCHEMA["properties"]["mappings"]["items"]["properties"]["target"]
    assert "null" in target["type"]


def test_response_schema_is_strict():
    items = RESPONSE_SCHEMA["properties"]["mappings"]["items"]
    assert items["additionalProperties"] is False
    for key in ("source", "target", "confidence", "entity", "reasoning"):
        assert key in items["required"]


# --- Learning loop integration ----------------------------------------------


def test_llm_answer_can_be_confirmed_into_alias_dictionary(schema):
    """An LLM-resolved header should become free on the next file."""
    client = MockLLMClient(responses={"Txn Ref": "Transaction Id"}, confidence=0.95)
    r = Resolver(schema, llm_client=client)

    first = r.resolve(["Txn Ref"])
    assert first.mappings[0].resolved_by == Layer.LLM
    r.confirm(first, "Txn Ref", "Transaction Id")

    second = r.resolve(["Txn Ref"])
    assert second.mappings[0].resolved_by == Layer.ALIAS
    assert len(client.calls) == 1   # no second model call
