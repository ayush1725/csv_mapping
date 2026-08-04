# When the LLM is Called, and What It Is

Companion to `project-plan.md`. Covers the invocation policy, the call
contract, the validation gate, and the question of whether any of this needs to
be an "agent".

---

## 1. Invocation policy

The LLM is called **per unseen header, once ever** — not per row, not per file.

```
file arrives
   |
   +-- 1. classify family            deterministic (Jaccard)     no LLM
   +-- 2. family template + delta    ~86% of headers             free
   +-- 3. L0-L2 normalize/alias/fuzzy typo tail                  free
   +-- 4. L3 embeddings              synonym tail                cheap
   |
   +-- 5. remainder ------------> ONE batched LLM call
   +-- 6. validator                 deterministic guardrails
   +-- 7. review queue -> human confirm -> alias dictionary
                                         (never asked again)
```

### Trigger conditions

The LLM is invoked for a header only when at least one holds:

| # | Condition | Source |
|---|---|---|
| T1 | Unresolved after L0–L3 | novel header |
| T2 | Resolved but below `accept_threshold` | weak match |
| T3 | Entity assignment is a guess | unmarked person block (F2) |
| T4 | File structure is novel for its family | new columns, reordering |
| T5 | Validator rejected a deterministic mapping | type/conflict failure |

Everything else never reaches the model.

### What does *not* trigger a call

- A file from a partner whose template is pinned and whose headers have not
  drifted — **zero calls**
- Any header already in the alias dictionary for that `(tenant, schema_id)`
- Repeat rows. Resolution is per *file header set*, never per row.

---

## 2. Volume and cost

A new partner in a known family sends ~60 headers, of which ~86% are already
known (measured, see `project-plan.md` §2.1), leaving ~8 unknown — **one
batched call**. The partner's template is then pinned; later files cost nothing
unless headers drift.

```
1000 partners x ~1 onboarding call        ~= 1000 calls, total, ever
naive per-row: 1000 x 10k rows x 60 cols  ~= 6e8 calls
```

**LLM cost scales with the number of distinct formats, not with files or
rows.** Formats grow far more slowly than partners: the sample shows 6 families
across 24 partners, with three exact-duplicate pairs.

Call volume also **decays**: every confirmed mapping enters the dictionary, so
the same header is never sent twice.

---

## 3. Call contract

### 3.1 Batch the whole file — never one header at a time

Forced by the data. MPGB and GGB repeat bare, unmarked
`Customer ID / Title / First Name / Last Name / DOB` three times for proposer,
insured and nominee. The three are byte-identical strings; only their ordinal
position separates them.

Sending headers individually destroys the single signal capable of resolving
them. The model must see the full ordered list.

### 3.2 System prompt — cacheable, identical on every call

- Role and task
- **Full canonical schema**: all 58 fields with type, required flag, entity,
  attribute. Not a shortlisted subset — shortlisting by string similarity prunes
  correct answers that have no lexical overlap (`Premium Amt` vs `Amount`,
  `APPOINTEE_NAME` vs `Guardian FirstName`).
- Resolution rules, in reliability order: explicit marker → sibling exclusion →
  block adjacency/position → abstain
- Hard rules: never invent a field; `null` is always permitted; prefer
  abstention to a guess

~900–1500 tokens, byte-identical per call, so it sits in the cacheable prefix.

### 3.3 User message

| Element | Why |
|---|---|
| `schema_id`, product | selects canonical schema |
| Partner name + family classification | prior |
| **Full ordered header list with indices** | position is evidence (F2) |
| Indices still unresolved | the actual question |
| Mappings already decided by L0–L3 | enables sibling exclusion |
| Redacted sample values for unresolved columns | type narrowing |

### 3.4 Output — structured, one entry per unresolved header

```json
{
  "mappings": [
    {
      "source_index": 36,
      "source_header": "First Name",
      "target": "First Name_Plan",
      "entity": "insured",
      "confidence": 0.88,
      "evidence": "position",
      "transform": "identity",
      "reasoning": "third of three unmarked person blocks; follows Sum Insured, precedes the nominee block"
    },
    {
      "source_index": 41,
      "source_header": "Nominee Name",
      "target": ["Nominee First Name", "Nominee Last Name"],
      "entity": "nominee",
      "confidence": 0.94,
      "evidence": "header_semantics",
      "transform": "split_name"
    },
    {
      "source_index": 17,
      "source_header": "col_17",
      "target": null,
      "confidence": 0.0,
      "reasoning": "opaque header; sample values are integers with no distinguishing format"
    }
  ]
}
```

`target: null` is a first-class answer and is explicitly encouraged. An extra
review row costs seconds; a wrong auto-applied mapping corrupts a policy
record.

`transform` is required because 1:1 mapping is refuted — `Nominee Name` arrives
as one column in 9 of 24 partners against two canonical fields.

---

## 4. Validation gate

LLM output is a **proposal**. It is never applied directly.

| Check | Action on failure |
|---|---|
| Target exists in schema | reject (hallucination) |
| Sample values match target type | reject — dates proposed for a `decimal` field |
| No duplicate target across columns | conflict → both to review |
| Entity consistent with the block's other fields | flag |
| All 9 required fields resolved | file gated |
| Confidence ≥ `accept_threshold` | below → suggestion only, stays in review |
| Transform is in the supported set | reject unknown transform |

Most of the system's safety lives here, and none of it is AI. An LLM outage
degrades to "everything goes to review", never to a wrong mapping.

---

## 5. Is this an agent?

**Mostly no, and that is the right answer.** For the large majority of cases a
single well-contexted structured call plus the validator outperforms a
multi-step agent: lower latency, lower cost, fewer failure modes, and a much
easier audit story for a regulated decision.

An agentic loop earns its place in exactly one situation: **the header carries
no information and the answer is in the data.** `col_17`, `Date1/Date2/Date3`,
`Loan A/c No` vs `Customer A/c No`.

There, provide tools:

| Tool | Resolves |
|---|---|
| `get_column_values(index, n)` | more samples than the default window |
| `check_format(index)` | PAN regex, mobile, ISO date, currency |
| `distinct_count(index)` | identifier vs categorical |
| `compare_columns(i, j)` | duplicate or correlated columns |
| `search_alias_history(header)` | has any other partner sent this header? |

Loop: propose → validate → revise, capped at ~3 iterations, with the same
validation gate applied to the final answer.

This is a **workflow with a validation loop**, not an autonomous agent. Naming
it accurately matters — it keeps the scope from inflating into something that
is harder to audit than the problem justifies.

### Build order

1. Single structured call + validator
2. Measure what the review queue is actually full of
3. Add tools only for the case classes the single call demonstrably cannot solve

Let the review queue decide what gets built, not what is technically
interesting.

---

## 6. Learning loop

```
LLM proposal -> validator -> review queue -> human confirms
                                   |
                                   v
                    alias dictionary, keyed by
                    (tenant, schema_id, family, normalized_header, position_role)
                                   |
                                   v
                    next occurrence resolves at Layer 1, free
```

Two properties follow:

- **Call volume decays** as the dictionary grows
- **Accuracy rises** where it matters most, because the headers that get
  confirmed are the ones that actually occur

Keying includes `position_role` so that MPGB's three identical `First Name`
columns are learned as three distinct entries rather than collapsing into one
wrong alias.

---

## 7. Failure modes and defaults

| Failure | Default behaviour |
|---|---|
| LLM timeout or error | header stays unresolved, file proceeds to review (`fail_open=True`) |
| Malformed structured output | discard, treat as abstention |
| Model proposes a field outside the schema | rejected by validator |
| Model is confidently wrong on entity | position and sibling checks in the validator; unmarked blocks are never auto-applied |
| Provider outage | deterministic layers continue unaffected; only the long tail degrades |

The system must be fully functional with the LLM disabled — degraded coverage,
never degraded correctness.
