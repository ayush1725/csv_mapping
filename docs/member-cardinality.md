# Member Cardinality — Up to 6 Members per Policy

Business rule: **a policy covers at most 6 members, and the proposer counts as
one of the 6.** So a policy carries the proposer plus **at most 5 additional
insured members**.

The create-policy API enforces this: a 7th member causes the call to fail.
**The exact status code is unconfirmed** — reported as "most likely 404, not
sure" — which is itself a finding, see §4.2.

This document records what the cap implies for the canonical schema, the entity
model, header resolution, and validation.

---

## 1. What the partner samples support today

| Partner | Lives the row can structurally hold | Markers |
|---|---|---|
| Rbl, House loader | **4** | proposer + `insured_adult2` + `insured_child1` + `insured_child2` |
| Hello ujjivan standalone | **2** | insured + `Insured 2 First Name/Last Name` |
| Remaining 21 partners | **1** | single insured block only |

**No sampled partner format can hold 6 members in a single row.** The widest
holds 4.

Combined with the observation that 21 of 24 partners send `No. of Lives` while
only 3 have room for more than one member (see `row-identity-analysis.md`),
this points to one of:

| # | Explanation | Consequence |
|---|---|---|
| a | Policies with 5–6 members arrive as **multiple rows** (long format) | Row grouping is mandatory before policy creation |
| b | These partners cap below 6 in practice | Wide format is sufficient for them |
| c | Additional members exist but are not itemised (floater) | Only the count and a shared sum insured are carried |

Unresolved from headers alone. The queries in `row-identity-analysis.md` §3
settle it.

---

## 2. The entity model expands

The canonical schema currently models **four** entities: proposer, insured,
nominee, guardian. A 6-member cap means the insured is not a single entity but
an **indexed group**.

```
before:  proposer · insured        · nominee · guardian
after:   proposer · insured[1..6]  · nominee · guardian
```

Entity resolution therefore becomes a **`(role, ordinal)`** decision rather than
a `role` decision.

`policy_v2` has exactly one insured block (the `_Plan` suffix). **It cannot
represent a policy with more than one insured member.** This is the same schema
gap identified for health products, now confirmed as a general rule rather than
a product-specific one.

### 2.1 Representation options

| Option | Shape | Assessment |
|---|---|---|
| **Wide** | 6 × insured blocks as columns (~30 extra fields) | Simple, no API change, but bloats the schema and most columns are empty most of the time |
| **Nested** | `members[]` array on the policy | Cleanest model; changes the create-policy contract |
| **Long** | One policy record + N member records | Matches long-format files naturally; largest change to the existing pipeline |

This is a schema decision, not a mapping decision, and should be resolved with
the create-policy API owner before build.

---

## 3. Ordinal assignment is a row-level problem

Partner ordinals are **role-relative**. Canonical ordinals are **absolute**.

```
Rbl sends:    insured_adult2   insured_child1   insured_child2
Canonical:    member 2         member 3         member 4
```

`child1` means *the first child*, not *member 1*. Mapping it to an absolute
member index depends on **how many adults that row contains**, which cannot be
determined from the column header in isolation.

**Consequence:** ordinal assignment is a **row-level** decision, whereas every
other mapping decision in this design is a **column-level** decision. The
resolver must therefore:

1. Resolve each member column to `(role, partner_ordinal)` at column level
2. Group the resolved columns into member blocks
3. Assign absolute member indices at row level, once the composition is known

Ordering rule must be defined explicitly — for example proposer first, then
adults by partner ordinal, then children by partner ordinal. Without a stated
rule the assignment is non-deterministic across partners.

### 3.1 Ordinal conventions observed

| Convention | Example | Meaning |
|---|---|---|
| Role + digit | `insured_adult2`, `insured_child1` | second adult, first child |
| Suffix code | `dob_2a`, `gender_c1`, `age_c2` | adult 2, child 1, child 2 |
| Plain index | `Insured 2 First Name` | second insured |
| Named suffix | `First Name_Plan` | the single insured |

These must all normalize to `(role, ordinal)` before row-level assignment.

---

## 4. Validation rules

| # | Rule | Action on breach |
|---|---|---|
| V1 | **Total persons including the proposer ≤ 6** (proposer + ≤5 insured) | Reject the row **pre-flight** with a specific reason code |
| V2 | **Declared `No. of Lives` == member blocks actually populated** | Hard-stop the row |
| V3 | Member ordinals are contiguous from 1 | Flag — a gap suggests a missed mapping |
| V4 | No duplicate absolute ordinal | Conflict — two columns claim the same member |
| V5 | Each populated member has the minimum required attributes | Flag incomplete member |

### 4.1 V2 is the important one

If a file declares `No. of Lives = 5` but only 4 member blocks resolve, either
the file is truncated **or the header mapping missed a block**. The result would
be a family that is **under-insured with no error raised** — the row processes
successfully and nobody notices until a claim.

This check is cheap, deterministic, and catches a failure mode that the
confidence thresholds alone would not: every individual mapping can be
high-confidence while the set is incomplete.

`No. of Lives` is present in 21 of 24 partners, so the check is broadly
applicable.

### 4.2 Enforce the cap pre-flight, never at the API

The create-policy API already rejects a 7th member, so it is tempting to let it
be the enforcement point. That is the wrong place, because of **where in the
pipeline the failure lands**:

```
upload accepted ─▶ rows enqueued to SQS ─▶ row 4,312 calls create-policy ─▶ ERROR
       ▲                                                                      │
       └──────────── partner was already told the file was accepted ──────────┘
```

An over-count discovered at the API is discovered **after** the file passed
upload, **after** rows were queued, and **per row, mid-batch**. The partner has
already been told the upload succeeded. Recovering means identifying which rows
failed, out of a batch, from an error the operator did not raise and may not be
able to interpret.

Validated at the pre-flight gate instead, the same condition is caught **before
enqueue**, against the whole file, with a message naming the row and the member
count. This is precisely the value of having a pre-flight stage at all.

**The API check remains as a backstop. It must not be the primary control.**

### 4.3 The unconfirmed error code is itself a risk

The status code returned for a 7th member is reported as "most likely 404, not
sure". Two problems follow, independent of this project:

| Problem | Consequence |
|---|---|
| **404 is semantically wrong** for a payload violation — 400 or 422 is the correct class | A 404 is indistinguishable from "endpoint not found", so a routing or deploy fault looks identical to a data fault |
| **If the code is unknown, current handling is also unknown** | Nobody can say today whether these rows are retried, dead-lettered, or silently dropped |

The second matters more. If the pipeline retries on 404, a 7-member row retries
forever. If it swallows the error, the row disappears with no record and the
partner is never told.

**Action:** submit a deliberate 7-member row against the create-policy API in a
non-production environment, record the exact status and body, and confirm what
the SQS consumer currently does with it. This is a short task and it establishes
how many such failures may already be occurring unnoticed.

---

## 5. Effect on the rest of the design

| Area | Effect |
|---|---|
| Canonical schema | Needs a repeating member group, max 6 — see §2.1 |
| Alias dictionary | Mapping value carries `member_ordinal` alongside `entity`; the key is unchanged |
| Entity resolution | Two-stage becomes three-stage: whose → which member → which attribute |
| Guard list | Cross-member pairs join cross-entity pairs as high-severity confusables (`dob_c1` vs `dob_c2`) |
| Row limit / SQS chunking | If long format is confirmed, a policy may span up to 6–7 rows; chunking must respect group boundaries |
| LLM prompt | Must state the cap and the ordinal convention, and return `(role, ordinal)` rather than a bare entity |
| Review queue | New reason codes: `member_count_mismatch`, `member_cap_exceeded`, `ordinal_ambiguous` |

---

## 6. Open questions

These are specification questions, not engineering ones, and each changes the
validation rules:

### Answered

| # | Question | Answer |
|---|---|---|
| Q1 | Does the cap of 6 include the proposer? | **Yes.** 6 total persons — proposer + at most 5 insured members |
| Q5 | What happens at 7? | The create-policy API rejects the call. Status code unconfirmed — see §4.2, §4.3 |

### Still open

| # | Question | Why it matters |
|---|---|---|
| **Q7** | **Does a partner's `No. of Lives` include the proposer — and is that consistent across partners?** | **Blocks V2.** If partner A's "5 lives" means 5 insured plus a proposer (6 total) and partner B's means 5 total, the same value implies different member counts. Likely varies by partner and may need to be a per-family setting |
| Q2 | Is the **nominee** counted toward the 6? | Nominee is modelled as a separate entity, not an insured life. Affects V1 arithmetic |
| Q3 | One nominee **per policy** or **per member**? | Determines whether nominee also becomes an indexed group |
| Q4 | Can each nominee have its own **guardian**? | Same question for the guardian entity |
| Q6 | Are there **role constraints** — maximum adults, maximum children, must the proposer be insured? | Additional validation rules |
| Q8 | Is a policy that legitimately has 7+ people ever **split into two policies**, or always rejected? | If splitting is valid, it is a business operation the resolver must never perform silently |

**Q7 is the new blocker.** V1 is now implementable; V2 is not, because the
member count implied by `No. of Lives` is ambiguous until the convention is
known. Given that 21 of 24 partners send the field, the convention should be
verified per family rather than assumed global.
