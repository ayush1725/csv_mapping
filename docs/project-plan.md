# Header Resolution — Project Plan

**Status:** Draft for review.
**Scope:** Automatic mapping of partner CSV upload headers to the canonical
policy schema, so bulk upload stops requiring manual header fixes.

---

## 1. The goal

> Partner count grows from ~10 to 1000+. **Person-hours spent fixing headers
> stays flat.** Wrong mappings stay at zero.

Three measurable outcomes:

| Metric | Today | Target |
|---|---|---|
| % of files needing a human header fix | TBD | TBD |
| Person-hours/week on header resolution | TBD | flat as partners grow |
| Wrong mappings reaching policy creation | TBD | 0 |

Every phase below serves one of these. Anything that serves none of them is cut.

---

## 2. Scope boundary

**In scope:** deciding which canonical field each uploaded column corresponds
to, and escalating what can't be decided confidently.

**Out of scope:** row-value validation, the SQS pipeline, the create-policy
API, and policy business rules. This is a pre-flight gate; everything
downstream is untouched.

```
Upload CSV -> [header resolution] -> confident   -> auto-remap -> SQS -> create-policy API
                                  -> uncertain   -> review     -^
                                  -> confirmed   -> alias dictionary (learning loop)
```

---

## 3. Phases

### Phase 0 — Frame the problem (week 1)

Answer first, honestly: **is downstream mapping the right fix at all?** If a
partner template exists and is ignored, upstream enforcement may be higher
leverage. The answer is usually "both", but it must be asked before building.

Work:
- Stakeholder map — operations, product, engineering, risk/compliance
- One walkthrough session with operations: a single real partner file that
  needed manual fixing, followed arrival → policy created
- Capture: who noticed, who fixed, how they knew the correct mapping, whether
  the fix was remembered for the next file

**Exit:** written problem statement and an agreed target number.

See `requirements-discovery.md` for the full interview checklist.

---

### Phase 1 — Mine the ground truth (weeks 1–2, parallel with Phase 0)

The highest-value work in the project, and it is not AI work.

- Collect historical partner files — as many partners as possible, target 100+
  files
- Collect whatever record exists of how each was fixed: edited CSVs, scripts,
  mapping screens, tribal knowledge
- Build a labelled set: `(partner, source header, sample values, correct field)`

Then measure the number that decides the architecture:

> **What fraction of incoming headers are exact repeats of headers already
> seen?**

If it is high, a learned dictionary resolves most traffic and the LLM is a
long-tail tool. If it is low, semantic matching carries the load. This cannot
be guessed, and every threshold chosen before it is arbitrary.

**Exit:** ≥300 labelled header→field pairs, plus the repeat-rate figure.

**This phase is a gate, not a task.** Calibration and honest evaluation are
impossible without it.

---

### Phase 2 — Lock the design decisions (week 3)

Each needs a named owner and a written answer.

| # | Decision | Why it matters |
|---|---|---|
| D1 | When does mapping run — per upload, once at onboarding, or hybrid? | Determines runtime service vs. configuration tool — different products |
| D2 | Who resolves what the system can't — ops queue, partner bounce, or fully automatic? | If no one owns a queue, "flag for human" is a dead end |
| D3 | What does a wrong mapping cost — detectable, reversible, regulated? | Sets the auto-apply threshold, currently a guess |
| D4 | What happens to a partially resolvable file — reject, hold, or quarantine the rest? | State management and partner experience |
| D5 | Policy only, or shared with claims/renewals/commissions? | Library vs. standalone service |

Recommendation on D1: **hybrid** — pin a mapping per partner at onboarding,
run drift detection per upload.

**Exit:** dated decision log with owners.

---

### Phase 3 — Architecture (week 4)

Default proposal, offered to be argued with rather than accepted:

- **Standalone header-resolution service**, called by the portal at upload
  time, **before enqueue**. The existing SQS pipeline is untouched.
- **Alias dictionary** in Postgres, scoped by `(tenant, schema_id)`. Scoping is
  mandatory: `amount` means premium in policy and claim amount in claims, and a
  flat dictionary lets domains poison each other.
- **Tiered resolver**, cheapest layer first (section 4).
- **Partner mapping template** pinned at onboarding, drift-detected per upload.
- **Review queue** with a thin operations UI.
- **Audit log of every decision** — source header, chosen field, confidence,
  resolving layer, confirming user, timestamp.

**Exit:** design doc, API contract, data model.

---

### Phase 4 — Build in thin slices (weeks 5–10)

| Slice | Content | Independently shippable |
|---|---|---|
| S1 | Normalize + alias dictionary + fuzzy + audit log | Yes — contains no AI |
| S2 | Review UI + confirm loop writing back to the dictionary | Yes |
| S3 | LLM layer, opt-in behind a flag | Yes |
| S4 | Partner template pinning + drift detection | Yes |
| S5 | Embedding layer | Optional — cost optimisation only |

Each slice ends green against the Phase 1 eval set before the next begins.

---

### Phase 5 — Calibrate (week 11)

Set thresholds from the eval set rather than intuition.

- Optimise for **zero wrong auto-applies first**, then maximise coverage under
  that constraint
- Report precision and coverage **per layer** — they fail in different ways
- Record the chosen operating point and the evidence for it

---

### Phase 6 — Shadow mode (weeks 12–15)

Run in production, produce mappings, **apply nothing**. Compare every
suggestion against what the human actually did.

This is the only honest validation available before launch.

**Exit:** agreement rate above target, zero wrong mappings observed in shadow.

---

### Phase 7 — Staged rollout

Widen trust one step at a time, each with a kill switch:

1. Auto-apply **alias-layer hits only** — exact matches on previously confirmed
   mappings, the safest possible class
2. Add fuzzy matches above threshold
3. Add LLM-suggested mappings above threshold

Enable per partner, starting with the highest-volume cooperative partner.

---

### Phase 8 — Scale out

- Extend the shared service to claims, renewals, commissions
- Partner self-service mapping at onboarding
- Feed recurring mismatches back to partners as template corrections

---

## 4. Why a tiered resolver

Sending every header to an LLM is slow, costly per file, and hard to trust for
insurance data. Cheap deterministic methods should resolve the easy majority,
leaving the model only what genuinely needs judgment.

| Layer | Method | AI cost |
|---|---|---|
| 0 | Normalize — casing, separators, camelCase, abbreviations | none |
| 1 | Alias dictionary — exact lookup, grown from confirmed mappings | none |
| 2 | Fuzzy — string similarity, handles typos and spacing | none |
| 3 | Embeddings — semantic similarity | cheap |
| 4 | LLM — full schema plus sample values, structured output, may abstain | per call |

**The learning loop is the part that scales.** Every confirmed mapping is
written back to the alias dictionary, so the free layer covers more traffic
over time and model usage falls as partner count rises. Without it, cost grows
linearly with partners — which is the problem being solved.

---

## 5. Known structural risk in the schema

The canonical schema packs **four different people into one row** — proposer,
insured, nominee, guardian. Person-attributes such as `First Name` are
ambiguous across all four.

The failure is **systematic, not random**: the unqualified variant is the
shortest string, so plain similarity matching favours the same entity every
time, regardless of what the file means. The consequence is the wrong person
being insured.

Resolution must therefore be two-stage — decide *whose* column this is, then
*which* attribute — using explicit markers, sibling exclusion, and column
adjacency. Where none of those apply, the mapping is flagged rather than
applied.

This is the single most likely source of a confidently wrong mapping and should
be raised with risk/compliance in Phase 2, alongside D3.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| A wrong mapping silently creates a bad policy | Abstain by default, guard list on confusable fields, audit log, shadow mode before any auto-apply |
| No usable ground truth exists | Phase 1 is a gate; without it thresholds cannot be justified |
| No owner for the review queue | Force D2 to an answer in week 3 |
| Policy-holder data sent to an external model | Send headers plus redacted samples only, or run in-region on a managed provider; confirm with compliance |
| Root cause is an unenforced partner template | Asked explicitly in Phase 0 |
| Schema changes mid-project | Schema is an input keyed by `schema_id`, never a constant |

---

## 7. Minimum viable cut

If the timeline compresses, ship **S1 + S2 only — no LLM**.

Deterministic matching plus a learning loop where every human confirmation
feeds the alias dictionary. If header repetition across partner files is as
high as it usually is, this alone removes most of the manual effort, with no
model cost, no latency risk, and no data-residency question.

Add the LLM once the dictionary stops growing and only the genuine long tail
remains — that is the point where per-call cost is actually justified.

---

## 8. Status of existing code

The prototype in this repository is a **feasibility probe**, not a committed
design. Its matching mechanics are reusable; its orchestration — placement,
thresholds, review model, service boundary — is assumption pending Phase 2.

What it did establish, which holds regardless of business flow because it is a
property of the master data:

- 58 canonical fields — small enough that no vector database is required
- 47 confusable field pairs, 12 of them spanning different people
- Unqualified person-attributes fail systematically rather than randomly
