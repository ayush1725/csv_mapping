#!/usr/bin/env python3
"""Run the resolver over a deliberately messy partner file and show the funnel.

    python demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from header_resolver import (  # noqa: E402
    MockLLMClient,
    Resolver,
    Schema,
    build_guard_list,
)

# A realistic partner upload: typos, abbreviations, synonyms, reordered words,
# a meaningless column, and bare person-attributes with no entity marker.
MESSY_HEADERS = [
    "Loan Account Number",
    "Applicatn Date",
    "Brnch Code",
    "Customer ID",
    "First Name",
    "Last Name",
    "DOB",
    "Mobile",
    "Email",
    "Sum Assured",
    "Nominee First Name",
    "Nominee Last Name",
    "Nominee DOB",
    "Nominee Relationship",
    "Guardian FirstName",
    "col_17",
    "Premium Amt",
    "Occupation",
    "Annual Income",
    "PEP",
]


# Sample values for the opaque columns — the strongest signal Layer 4 gets.
SAMPLES = {
    "col_17": ["2020-01-15", "2019-11-03", "2021-06-22"],
    "Premium Amt": ["1250.00", "980.50", "3400.75"],
    "Mobile": ["9876543210", "9123456789"],
}

# Stand-in for a real model, so the demo runs with no credentials. Swap for
#   AnthropicClient(provider="bedrock", aws_region="ap-south-1")
# to exercise the real thing.
FAKE_LLM_ANSWERS = {
    "col_17": "Transaction Date",
    "Premium Amt": "Amount",
}


def main() -> None:
    schema = Schema.load_builtin("policy_v2")
    guards = build_guard_list(schema)
    llm = MockLLMClient(responses=FAKE_LLM_ANSWERS, confidence=0.91)
    resolver = Resolver(schema, llm_client=llm)

    print(f"schema      : {schema.schema_id}  ({len(schema.fields)} fields)")
    print(f"entities    : {', '.join(schema.entities)}")
    print(
        f"guard list  : {len(guards)} confusable pairs "
        f"({sum(1 for p in guards.pairs if p.cross_entity)} cross-entity)\n"
    )

    # Deterministic only, then again with Layer 4, to show what it adds.
    before = resolver.resolve(MESSY_HEADERS, tenant="demo_bank", use_llm=False)
    result = resolver.resolve(MESSY_HEADERS, tenant="demo_bank", samples=SAMPLES)

    print(f"{'partner header':<24} {'->':<3} {'canonical':<24} {'conf':<6} {'layer':<11} flags")
    print("-" * 100)
    for m in result.mappings:
        flags = ", ".join(r.value for r in m.review_reasons) or ""
        target = m.target or "—"
        print(
            f"{m.source:<24} {'->':<3} {target:<24} "
            f"{m.confidence:<6.2f} {m.resolved_by.value:<11} {flags}"
        )

    total = len(result.mappings)
    auto = len(result.auto_appliable)
    print("\n" + "=" * 100)
    print(f"auto-applied     : {len(before.auto_appliable)}/{total} "
          f"deterministic  ->  {auto}/{total} with Layer 4")
    print(f"human review     : {len(before.review_queue)}/{total} "
          f"deterministic  ->  {len(result.review_queue)}/{total} with Layer 4")
    print(f"layer breakdown  : {result.layer_counts()}")
    print(f"layer 4          : {result.layer4}")
    if result.unmapped_required:
        print(f"MISSING REQUIRED : {', '.join(result.unmapped_required)}")
    if result.conflicts:
        print(f"conflicts        : {result.conflicts}")

    print("\nLayer 4 saw only the review queue — never the columns already resolved.")


if __name__ == "__main__":
    main()
