# Member Cardinality — Up to 6 Members per Policy

Business rule: **a policy covers at most 6 members.** This document records
what that implies for the canonical schema, the entity model, header
resolution, and validation.

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
| V1 | Members per policy ≤ 6 | Reject the row with a specific reason code |
| V2 | **Declared `No. of Lives` == member blocks actually populated** | Hard-stop the row |
| V3 | Member ordinals are contiguous from 1 | Flag — a gap suggests a missed mapping |
| V4 | No duplicate absolute ordinal | Conflict — two columns claim the same member |
| V5 | Each populated member has the minimum required attributes | Flag incomplete member |

### V2 is the important one

If a file declares `No. of Lives = 5` but only 4 member blocks resolve, either
the file is truncated **or the header mapping missed a block**. The result would
be a family that is **under-insured with no error raised** — the row processes
successfully and nobody notices until a claim.

This check is cheap, deterministic, and catches a failure mode that the
confidence thresholds alone would not: every individual mapping can be
high-confidence while the set is incomplete.

`No. of Lives` is present in 21 of 24 partners, so the check is broadly
applicable.

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

| # | Question | Why it matters |
|---|---|---|
| Q1 | Does the cap of 6 **include the proposer**, or is it 6 insured members plus the proposer? | Changes V1 from `≤6` to `≤7` total persons |
| Q2 | Is the **nominee** counted as a member? | Nominee is currently a separate entity, not an insured life |
| Q3 | One nominee **per policy** or **per member**? | Determines whether nominee also becomes an indexed group |
| Q4 | Can each nominee have its own **guardian**? | Same question for the guardian entity |
| Q5 | What happens at 7 members — reject the file, reject the row, or split into two policies? | Determines error handling and whether splitting is ever legitimate |
| Q6 | Are there **role constraints** — e.g. maximum adults, maximum children, must include the proposer? | Additional validation rules |

Q1 and Q5 block implementation of V1.
