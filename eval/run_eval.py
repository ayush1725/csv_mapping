#!/usr/bin/env python3
"""Score the resolver against a labelled set — with or without a real model.

    python eval/run_eval.py                          # Layers 0-2 only, no credentials
    python eval/run_eval.py --provider direct        # + Layer 4, needs ANTHROPIC_API_KEY
    python eval/run_eval.py --provider bedrock --region ap-south-1

Accuracy is not a single number here, because the failure modes are not equally
bad. A WRONG mapping silently corrupts policy records; an ABSTENTION costs a
few seconds of review. They are reported separately and never averaged
together.

    precision   of the headers it answered, how many were right
    coverage    how often it answered at all
    unsafe      confident answers on headers that should have been abstentions

`unsafe` is the number to watch. It should be zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from header_resolver import Resolver, Schema  # noqa: E402

CASES_PATH = Path(__file__).parent / "cases.json"


def build_client(provider: str, region: str | None, model: str, effort: str):
    if provider == "none":
        return None
    from header_resolver import AnthropicClient

    return AnthropicClient(
        provider=provider, aws_region=region, model=model, effort=effort
    )


def classify(case: dict, mapping) -> str:
    """One of: correct, wrong, unsafe, abstained_ok, abstained_miss."""
    expected = case.get("expect")
    got = mapping.target if not mapping.needs_review else None

    if expected is None:
        # Abstention was the correct answer.
        return "abstained_ok" if got is None else "unsafe"

    if got is None:
        return "abstained_miss"      # safe, but it should have known
    return "correct" if got == expected else "wrong"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="none", choices=["none", "direct", "bedrock"])
    ap.add_argument("--region", default=None, help="AWS region, for --provider bedrock")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--verbose", action="store_true", help="print every case")
    args = ap.parse_args()

    if args.provider == "bedrock" and not args.region:
        ap.error("--region is required with --provider bedrock")

    spec = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = spec["cases"]
    schema = Schema.load_builtin(spec["schema_id"])

    client = build_client(args.provider, args.region, args.model, args.effort)
    resolver = Resolver(schema, llm_client=client)

    label = f"{args.provider} ({args.model}, effort={args.effort})" if client else "deterministic only (Layers 0-2)"
    print(f"schema : {schema.schema_id} ({len(schema.fields)} fields)")
    print(f"mode   : {label}")
    print(f"cases  : {len(cases)}\n")

    # Each case is resolved on its own so no sibling can leak context between
    # them — several cases exist precisely to test the no-sibling situation.
    started = time.time()
    outcomes: list[tuple[dict, object, str]] = []
    for case in cases:
        samples = {case["header"]: case["samples"]} if case.get("samples") else None
        res = resolver.resolve([case["header"]], samples=samples)
        m = res.mappings[0]
        outcomes.append((case, m, classify(case, m)))
    elapsed = time.time() - started

    counts: dict[str, int] = defaultdict(int)
    by_group: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for case, _m, verdict in outcomes:
        counts[verdict] += 1
        by_group[case["group"]][verdict] += 1

    if args.verbose or counts["wrong"] or counts["unsafe"]:
        print(f"{'verdict':<15} {'header':<26} {'expected':<26} got")
        print("-" * 104)
        for case, m, verdict in outcomes:
            if not args.verbose and verdict not in ("wrong", "unsafe"):
                continue
            got = m.target if not m.needs_review else f"(review) {m.target or '—'}"
            mark = {"correct": "ok", "abstained_ok": "ok (abstained)",
                    "abstained_miss": "missed", "wrong": "WRONG",
                    "unsafe": "UNSAFE"}[verdict]
            print(f"{mark:<15} {case['header']:<26} {str(case.get('expect') or '(abstain)')[:24]:<26} {got}")
        print()

    print(f"{'group':<24} {'correct':>8} {'wrong':>7} {'unsafe':>7} {'missed':>8} {'abst-ok':>8}")
    print("-" * 68)
    for group in sorted(by_group):
        g = by_group[group]
        print(
            f"{group:<24} {g['correct']:>8} {g['wrong']:>7} {g['unsafe']:>7} "
            f"{g['abstained_miss']:>8} {g['abstained_ok']:>8}"
        )

    answered = counts["correct"] + counts["wrong"] + counts["unsafe"]
    total = len(cases)
    precision = counts["correct"] / answered if answered else 0.0
    coverage = answered / total

    print("\n" + "=" * 68)
    print(f"precision   {precision:6.1%}   of answered headers, how many were right")
    print(f"coverage    {coverage:6.1%}   how often it answered at all")
    print(f"UNSAFE      {counts['unsafe']:6d}   confident answers that should have abstained")
    print(f"wrong       {counts['wrong']:6d}   confident answers to the wrong field")
    print(f"missed      {counts['abstained_miss']:6d}   abstained when it should have known")
    print(f"\nwall clock  {elapsed:6.1f}s")

    if client is not None:
        u = client.usage
        print(
            f"llm calls   {u.calls:6d}   "
            f"{u.input_tokens} in / {u.output_tokens} out tokens"
        )

    # Non-zero exit on any unsafe answer, so this can gate a deploy.
    if counts["unsafe"]:
        print("\nFAIL — unsafe answers present. Do not enable auto-apply.")
        return 1
    print("\nPASS — no unsafe answers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
