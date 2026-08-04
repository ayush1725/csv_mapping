#!/usr/bin/env python3
"""Run the resolver over a deliberately messy partner file and show the funnel.

    python demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from header_resolver import Resolver, Schema, build_guard_list  # noqa: E402

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


def main() -> None:
    schema = Schema.load_builtin("policy_v2")
    guards = build_guard_list(schema)
    resolver = Resolver(schema)

    print(f"schema      : {schema.schema_id}  ({len(schema.fields)} fields)")
    print(f"entities    : {', '.join(schema.entities)}")
    print(
        f"guard list  : {len(guards)} confusable pairs "
        f"({sum(1 for p in guards.pairs if p.cross_entity)} cross-entity)\n"
    )

    result = resolver.resolve(MESSY_HEADERS, tenant="demo_bank")

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
    print(f"auto-applied     : {auto}/{total}")
    print(f"human review     : {len(result.review_queue)}/{total}")
    print(f"layer breakdown  : {result.layer_counts()}")
    if result.unmapped_required:
        print(f"MISSING REQUIRED : {', '.join(result.unmapped_required)}")
    if result.conflicts:
        print(f"conflicts        : {result.conflicts}")

    print("\nThe review queue is exactly what Layers 3-4 (embeddings, LLM) will consume.")


if __name__ == "__main__":
    main()
