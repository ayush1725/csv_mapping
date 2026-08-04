# header-resolver

Auto-maps partner CSV upload headers to a canonical schema, so bulk uploads
stop needing manual header fixes.

Partners upload policy CSVs whose column headers don't match ours. Resolving
those by hand works for a handful of partners and breaks down at 1000+. This
resolves them with a tiered pipeline that keeps a human in the loop only where
it matters.

**Status:** Layers 0–2 implemented and tested. Layers 3–4 (embeddings, LLM) are
not built yet — headers this pipeline can't resolve confidently are emitted as
`UNRESOLVED` with `needs_review=True`, which is exactly the queue those layers
will consume.

---

## Why tiered

Calling an LLM for every header is slow, costly, and hard to trust for
insurance data. Cheap deterministic methods resolve the easy majority; the
model only sees headers that genuinely need judgment.

| Layer | Method | AI cost |
|---|---|---|
| 0. Normalize | casing, separators, camelCase, abbreviation expansion | none |
| 1. Alias dictionary | exact lookup, grown from confirmed mappings | none |
| 2. Fuzzy | Jaro-Winkler / ratio / token-sort, best-of | none |
| 3. Embeddings *(not built)* | cosine similarity vs canonical fields | cheap |
| 4. LLM *(not built)* | header + sample values + top-K candidates | per-call |

Every confirmed mapping is written back to the alias dictionary, so the free
layer covers more traffic over time and the model is needed less.

---

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

```python
from header_resolver import Resolver, Schema

schema = Schema.load_builtin("policy_v2")
resolver = Resolver(schema)

result = resolver.resolve([
    "Loan Account Number", "Applicatn Date", "Nominee Name", "col_17",
])

for m in result.mappings:
    print(m.source, "->", m.target, m.confidence, m.resolved_by.value, m.needs_review)
```

```
Loan Account Number -> Loan A/c No       1.00 alias      False
Applicatn Date      -> Application Date  0.97 fuzzy      False
Nominee Name        -> Nominee First Name 0.93 fuzzy     True   # ambiguous: first or last?
col_17              -> None              0.00 unresolved True   # needs Layer 3/4
```

Feed a human decision back in:

```python
resolver.confirm(result, "col_17", "Transaction Id")
# next file containing "col_17" resolves via ALIAS, for free
```

---

## The problem this schema actually has

`policy_v2` packs **four different people into one row** — proposer, insured
(`_Plan` suffix), nominee, and guardian. 17 of its 58 fields are
person-attributes.

That produces a *systematic*, not random, error. The unsuffixed variant is
always the shortest string, so plain fuzzy matching scores a bare `first_name`
highest against the **proposer** every single time — even in a file that means
the insured. Getting this wrong means the wrong person is insured.

So resolution is two-stage: decide **whose** column this is, then **which**
attribute. Three signals, in descending reliability:

1. **Explicit marker** — `Nominee First Name`, `First Name_Plan` → certain
2. **Sibling exclusion** — a file containing both `first_name` and
   `nominee_first_name` proves the bare one isn't the nominee → strong
3. **Block adjacency** — person blocks arrive contiguously, so a decided
   neighbour is real evidence

When none apply, the entity is a *guess* and the mapping is flagged rather than
applied. That is the case plain fuzzy silently gets wrong.

```python
resolver.resolve(["First Name"])
#   -> First Name, confidence 0.60, needs_review=True, [entity_ambiguous]

resolver.resolve(["First Name", "First Name_Plan", "Nominee First Name"])
#   -> all three clean at 1.00 — siblings disambiguate
```

---

## Guard list

Rather than hand-maintaining a blocklist of confusable fields, it's derived:
any two canonical fields scoring ≥80 against each other, plus any pair where
one field's tokens contain the other's. On `policy_v2` that's ~47 pairs.

```
Address Line 1     ~ Address Line 2     97.1
Customer ID        ~ Customer ID_Plan   95.8  [cross-entity]
DOB                ~ DOB_Plan           94.4  [cross-entity]
First Name         ~ First Name_Plan    93.3  [cross-entity]
```

Cross-entity pairs are the high-severity ones. A fuzzy match landing on either
side of a guarded pair must clear a wider margin before it can auto-apply, and
regenerates automatically when the schema changes.

---

## Thresholds

```python
from header_resolver import Resolver, Schema, Thresholds

Resolver(schema, thresholds=Thresholds(
    auto_apply=92.0,        # below this, route to review
    consider=70.0,          # below this, no candidate at all
    min_margin=8.0,         # required gap between top-2
    guard_margin=15.0,      # wider gap demanded for confusable fields
    entity_confidence=0.7,  # below this, entity assignment is untrusted
))
```

`auto_apply` sits at 92, not 80, because fuzzy's most dangerous errors outscore
many of its correct matches — `policy_start_date` vs `policy_end_date` scores
86.8. Tune these against a labelled eval set built from historical mappings
before loosening anything.

---

## Where it fits

A pre-flight gate. The existing queue and create-policy API are untouched:

```
Upload CSV -> [header resolution] -> high confidence  -> auto-remap -> SQS -> create-policy API
                                  -> low confidence   -> review UI  -^
                                  -> confirmed mapping -> alias dictionary
```

Intended to run as a standalone service so claims, renewals and commission
uploads share one alias dictionary rather than each building an isolated one.
Schema is an input (`schema_id`), not a constant, and everything learned is
scoped by `(tenant, schema_id)` — `amount` means `premium_amount` in policy and
`claim_amount` in claims, and one flat dictionary would let them poison each
other.

---

## Layout

```
src/header_resolver/
  normalize.py   Layer 0 — casing, camelCase, abbreviation expansion
  aliases.py     Layer 1 — alias dictionary + learning loop
  fuzzy.py       Layer 2 — multi-metric similarity
  guards.py      auto-derived confusable-pair list
  entities.py    two-stage entity resolution
  resolver.py    orchestration, thresholds, conflict/gap detection
  schema.py      canonical schema loading and indexing
  models.py      Mapping, ResolveResult, Layer, ReviewReason
data/
  policy_v2.json      the canonical schema
  abbreviations.json  domain abbreviations (a/c, DOB, PEP, RM, ...)
```

## Known limitations

- **8 of 58 fields are dates.** Value type-checking can't disambiguate
  `Application Date` from `Transaction Date` from `Loan Disbursal Date` —
  only header semantics and column position help.
- **Abbreviation expansion can create false similarity.** `RM Code` expands to
  `relationship_manager_code`, which legitimately scores 86 against
  `Relation`. Over-guarding costs review time but is never unsafe, so it's
  accepted rather than special-cased.
- **`Mobile` → `Mobile No` is flagged** despite being obviously correct
  (0.89, below the 92 threshold). Tightening this needs a real eval set, not
  a guessed threshold.
- **No sample-value type checking yet.** The `samples` argument is accepted
  and ignored; wiring it in is the cheapest remaining accuracy win.
