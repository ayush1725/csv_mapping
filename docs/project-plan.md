# Header Resolution — Project Plan

**Status:** Draft, revised after analysis of 24 partner sample header sets.
**Scope:** Automatic mapping of partner CSV upload headers to canonical policy
schemas, so bulk upload stops requiring manual header fixes.

---

## 1. The goal

> Partner count grows from ~10 to 1000+. **Person-hours spent fixing headers
> stays flat.** Wrong mappings stay at zero.

| Metric | Today | Target |
|---|---|---|
| % of files needing a human header fix | TBD | TBD |
| Person-hours/week on header resolution | TBD | flat as partners grow |
| Wrong mappings reaching policy creation | TBD | 0 |

---

## 2. What the partner samples establish

24 partner header sets analysed: 19 loan, 5 health, **0 travel** (travel was
named as a product category but no sample was supplied — treat as unknown).
1,113 header instances, 346 distinct after normalization.

### 2.1 The decisive number: 86% reuse

**86% of header instances are covered by headers appearing in two or more
partners.** 191 of 346 distinct headers (55%) are shared.

Most-shared headers and how many partners send them:

```
dob 22 · loan amount 19 · mobile no 19 · gender 17 · no of lives 17
loan tenure 16 · cover type 16 · pin code 16 · relation 16 · transaction id 15
```

**This validates the dictionary-first design.** The long tail is real but small.
An alias dictionary seeded from these 24 partners will resolve most columns of
partner 25 before any model is consulted.

### 2.2 Partners are not 24 formats — they are ~6 families

Jaccard similarity on normalized headers:

| Family | Members | Similarity |
|---|---|---|
| Vendor-identical | Rbl ≡ House loader; Namra ≡ Mathooth; Hello ujjivan feeder ≡ Data loader | **1.00** |
| Master-aligned | Allience, Avanti, Hello ujjivan feeder, Data loader | 0.64–0.69 |
| Bare-repeat | MPGB, GGB | 0.90 |
| Proposer/Applicant | Ubi, Boi, Hospital partner, Capri g, Uss, Uco, Agri feeder, Hello ujjivan standalone | 0.55–0.78 |
| camelCase health | Namra, Mathooth | 1.00 |
| Own-format singletons | L&T, Paisalo, Respo, IIFL, Tujbhavani, Sambhavati | — |

Three pairs are **exact duplicates** — the same upstream vendor system serving
multiple lenders.

**This is the biggest economic finding in the project.** Partner 1000 is
overwhelmingly likely to be a variant of a family that already exists, not a
new format. Onboarding becomes *"which family, then what's the delta"* rather
than *"map 60 columns from scratch."*

### 2.3 Coverage against master is bimodal

Exact-match coverage of the 58 canonical fields:

```
 91%  Allience                    <- master-aligned cluster
 84%  Hello ujjivan feeder / Data loader
 83%  Avanti
 ----------------------------------- cliff
 50%  GGB
 48%  MPGB
 33%  Respo, IIFL
 22%  Uco, Ubi, Tujbhavani, Boi, Agri feeder
 ...
  5%  Namra, Mathooth
  0%  Sambhavati
```

Two populations, not a gradient: partners who adopted the template, and
partners who send their own core system's export. **They need different
treatment** — the first needs drift detection, the second needs real mapping.

---

## 3. Findings that change the design

### F1 — 1:1 column-to-field mapping is refuted

`Nominee Name` arrives as a **single column in 9 partners**, but master has
`Nominee First Name` + `Nominee Last Name`. Same for Namra/Mathooth `name`,
Rbl `custname`, L&T `NOMINEE_NAME`.

**Splitting is not an edge case — it is roughly a third of the partner base.**
The mapping model must support one-source-to-many-target with a transform, not
just a field reference.

### F2 — Column position is load-bearing, not a tiebreak

MPGB and GGB repeat **bare, unmarked** `Customer ID / Title / First Name /
Last Name / DOB` **three times** in one row — proposer, then insured, then
nominee. No suffix, no prefix, no marker of any kind.

```
... Customer ID, Title, First Name, Last Name, DOB,        <- proposer
    ... Sum Insured, Customer ID, Relation, Title,
    First Name, Last Name, DOB,                            <- insured
    ... Product Plan Option, Loan Amount,
    Title, First Name, Last Name, Relation, DOB             <- nominee
```

Header text alone **cannot** resolve these — the three are byte-identical. Only
ordinal position and block structure can. Any design keyed purely on header
string will map all three to the proposer and silently insure the wrong person.

The resolver's unit must therefore be **(position, header)**, not `header`.

### F3 — The master schema has gaps, and they are common fields

Headers many partners send that have **no canonical field at all**:

| Partner header | Partners | Issue |
|---|---|---|
| `Gender` | **17** | No Gender field exists in master |
| `Cover Type` (Individual/Floater) | 16 | Referenced in master's own Sum Insured description, but not a field |
| `Policy Tenure` | 9 | Master has one `Tenure`; partners send Loan Tenure *and* Policy Tenure as separate columns |
| `Actual Premium` vs `Transaction Amount` | 8 / 7 | Master has one `Amount` — which is it? |
| `Good Health Declaration` | 7 | Underwriting-relevant |
| `PED` block (flag / remarks / month) | 6 | Pre-existing disease |
| `State` | 6 | Master has City and Pin Code but no State |
| `Proposal No` | 6 | |

**`Gender` being absent from a life/health master schema is worth escalating on
its own.** These are not mapping failures — the target does not exist. No AI
can fix that. It needs a product decision: extend the schema, or explicitly
document these as dropped.

`Tenure` is the sharpest trap: master's single `Tenure` is genuinely ambiguous
against two distinct partner columns, and mapping it wrong silently changes
policy duration.

### F4 — Health is a different schema, not a variant

Rbl and House loader carry **repeating insured groups**:

```
insured_adult2, gender_2a, relationship_2a, dob_2a, age_2a
insured_child1, gender_c1, relationship_c1, dob_c1, age_c1
insured_child2, gender_c2, relationship_c2, dob_c2, age_c2
```

Hello ujjivan standalone has `Insured 2 First Name` / `Insured 2 Last Name`.

`policy_v2` models **exactly one** insured (`_Plan`). A multi-life health row
cannot be represented at all. This is a schema gap, not a header problem, and
it confirms that `schema_id` must be a per-product input: `loan_v2`,
`health_v1`, `travel_v1`.

### F5 — Synonyms with zero string overlap justify embeddings

| Partner | Canonical | Fuzzy score |
|---|---|---|
| `APPOINTEE_NAME` (L&T) | `Guardian FirstName` | ~0 |
| `SUM_ASSURED` / `SumAssured` | `Sum Insured` | low |
| `GWP` (Paisalo) | `Amount` | 0 |
| `custname` (Rbl) | `First Name` | low |
| `constructType` (Namra) | `Cover Type` | low |

*Appointee* is the correct insurance term for a minor nominee's guardian.
String similarity will never find it; a domain-aware embedding or the LLM will.
**This is where your embedding-layer instinct is exactly right** — these cases
are semantically obvious and lexically invisible.

### F6 — Typos are systematic, and cheap to fix

`Propsoal no`, `Aplication Date`, `Intetemediry Code`, `Intemediary Code`,
`Nomini Name`, `Transation Date`, `Disbursment Date`, `Loan disbusrement date`,
`Convalescene`, `serco_disposiotion`, `Booking Scop-Secondry`.

Pure Layer 2 fuzzy territory — no model needed. Note `Member No.` vs canonical
`Member Number`: 6 partners send it, none verbatim. Fuzzy recovers it.

### F7 — Header junk is real and must be tolerated

- Entire declaration paragraphs used as column headers (~80 words), in 7 partners
- Leaked BI references: `'GTLLead'[FirstameComplete]`, `'GTLLead"[FirstName]LastName3`
- Embedded business rules: `Policy Tenure ( Cannot be less than Loan tenure subject to maximum of 5 years)`
- Merged concepts: `AadharAddressLine1`, `Convalescence Benefit/EMI Value`
- Duplicate columns within one file (Mathooth `memberId` twice, Uss `Gender` twice)

Normalization must truncate at the parenthetical and cap header length before
any similarity scoring, or these dominate the distance metric.

---

## 4. Revised architecture

### 4.1 Two-level mapping — the main change

Do not resolve every file from scratch. Resolve against a **family template**,
then a **partner delta**.

```
                 ┌── family template (≈6, hand-verified, versioned)
partner file ────┤
                 └── partner delta (only where partner differs from family)
                             │
                    unresolved remainder ──► tiered resolver ──► review
```

Onboarding partner 1000 becomes: classify into a family (cheap, high-signal
given 0.55–1.00 intra-family similarity), inherit its verified template, and
review only the delta. Given 86% reuse, the delta is small.

This is what makes 1000+ partners tractable. A flat per-partner mapping table
scales linearly with partners; family templates scale with **formats**, and
formats are growing far more slowly.

### 4.2 Resolver layers, with evidence-based allocation

| Layer | Method | Expected load | Evidence |
|---|---|---|---|
| 0 | Normalize — case, separators, camelCase, abbreviations, **truncate parentheticals** | all | F7 |
| 1 | Family template + partner alias dictionary | **the bulk** | 86% reuse, §2.1 |
| 2 | Fuzzy | typo tail | F6 |
| 3 | **Embeddings** | synonym tail | F5 |
| 4 | LLM | entity assignment, novel structures, splits | F1, F2 |

Your instinct — dictionary and embeddings before the LLM — is confirmed by the
data. The LLM's real job is narrower than "map headers": it is **entity
assignment and structural decisions**, which is precisely where wrong answers
create wrong policies.

### 4.3 Mapping model must support transforms

Minimum viable transform set, driven by the samples:

- `split_name` — `Nominee Name` → first + last (F1)
- `identity` — the default
- `constant` — partner always sends the same plan code
- `derive` — `age_2a` from `dob_2a` if needed

### 4.4 Position-aware resolution

Every source column carries its **ordinal index**. Entity assignment uses, in
descending reliability: explicit marker → sibling exclusion → **block
adjacency/position** → LLM judgment. F2 makes position mandatory rather than
supplementary.

### 4.5 Per-product schemas

`loan_v2` (today's `policy_v2`), `health_v1` (repeating insured groups),
`travel_v1` (unknown — no sample). Alias dictionary scoped by
`(tenant, schema_id)`.

---

## 5. Revised assumption register

| # | Assumption | Verdict |
|---|---|---|
| A7 | Mapping is 1:1 column → field | **REJECTED** — F1, splits affect ~⅓ of partners |
| A6 | One stable canonical schema | **REJECTED** — F4, needs per-product schemas |
| A9 | Header text + sample values are sufficient evidence | **REJECTED** — F2, position is decisive |
| A1 | Resolution runs per upload | **MODIFIED** — family template at onboarding, drift detection per upload |
| A2 | An internal ops team works a review queue | Open — D2 |
| A3 | Sits before SQS as a pre-flight gate | Provisionally confirmed |
| A4 | Unresolved headers wait for review | Open — D4 |
| A5 | Missing required field is a hard stop | Refine: gate on the **9 required fields**, not all 58 |
| A8 | Auto-apply threshold of 92 | Open — needs D3 and a real eval set |
| A10 | Worth solving downstream | Confirmed — partners clearly cannot conform; 4 of 24 adopted the template |

---

## 6. Phases

### Phase 0 — Frame the problem (week 1)

Walkthrough with operations following one real partner file end to end.
Stakeholder map: operations, product, engineering, risk/compliance.
See `requirements-discovery.md` for the interview checklist.

**Exit:** problem statement and an agreed target number.

### Phase 1 — Ground truth (weeks 1–2) — *partially complete*

Done: 24 partner header sets analysed, families identified, reuse measured.

Still required:
- **Verify the reconstructed header lists against original CSV/XLSX.** The
  supplied PDF lost header boundaries; `data/partner_samples.json` is a
  best-effort reconstruction and is not eval-grade.
- **Travel samples** — a named product category with zero samples.
- **Sample values** per partner — needed to separate the 22 type-separable
  confusable pairs (§8).
- **Historical manual fixes** — the actual labels.

**Exit:** verified header sets, ≥300 labelled header→field pairs with the
correct entity, travel samples or a decision to defer travel.

### Phase 2 — Decisions (week 3)

| # | Decision |
|---|---|
| D1 | When mapping runs — recommend **hybrid**: family template at onboarding, drift detection per upload |
| D2 | Who resolves what the system can't |
| D3 | Cost of a wrong mapping — sets the auto-apply threshold |
| D4 | Partial-file behaviour — recommend gate on the **9 required fields** only |
| D5 | Product scope — loan / health / travel schemas |
| **D6** | **Schema gaps (F3): add `Gender`, `Cover Type`, `Policy Tenure`, `State`, PED block — or explicitly drop them?** |
| **D7** | **Does master `Amount` mean premium, transaction amount, or actual premium?** |

D6 and D7 are new and blocking: no amount of AI resolves a column with no
target, and `Amount` is a required field whose meaning is currently ambiguous
against three distinct partner columns.

### Phase 3 — Architecture (week 4)

Standalone service, called before enqueue. Family templates + partner deltas.
Position-aware resolver. Transform support. Per-product schemas. Full audit log
of every decision.

### Phase 4 — Build in slices (weeks 5–10)

| Slice | Content | Ships alone |
|---|---|---|
| S1 | Normalize + family templates + alias dictionary + audit log | **yes — no AI** |
| S2 | Review UI + confirm loop feeding the dictionary | yes |
| S3 | Fuzzy + position-aware entity resolution | yes |
| S4 | Embeddings (F5 synonym tail) | yes |
| S5 | LLM for entity assignment and novel structures | yes |
| S6 | Transforms (split_name et al.) | yes |

### Phase 5 — Calibrate (week 11)

Thresholds from the eval set. Optimise for **zero wrong auto-applies first**,
then maximise coverage. Report precision/coverage per layer.

### Phase 6 — Shadow mode (weeks 12–15)

Produce mappings in production, apply nothing, compare against human decisions.

### Phase 7 — Staged rollout

Family-template hits → dictionary hits → fuzzy above threshold → embeddings →
LLM. Per-partner enablement, kill switch at each step. **Start with the
master-aligned family** (Allience, Avanti, Data loader, Hello ujjivan feeder) —
83–91% coverage means near-zero risk and immediate volume.

### Phase 8 — Scale out

Claims, renewals, commissions. Partner self-service mapping. Feed recurring
mismatches back to partners as template corrections.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Wrong entity → wrong person insured (F2) | Position-aware resolution; flag rather than apply when entity is a guess; never auto-apply an unmarked repeated block |
| Schema gaps silently drop data (F3) | D6 before build; unmapped-but-populated columns must be reported, never silently discarded |
| Health multi-life cannot be represented (F4) | Separate `health_v1` schema; do not force into `loan_v2` |
| Reconstructed samples are wrong | Phase 1 verification against original files before any eval use |
| Travel is unanalysed | Flagged; no design claim made for travel |
| No usable ground truth | Phase 1 is a gate |
| PII to an external model | Headers plus redacted samples only, or in-region hosting |

---

## 8. Schema properties (from master data)

- 58 fields; **9 required**: Member Number, Application Date, Loan A/c No,
  Customer ID, First Name, Last Name, DOB, Amount, Sum Insured
- 18 person-fields across 4 entities (proposer 5, insured 5, nominee 5,
  guardian 3)
- 47 confusable pairs, 12 cross-entity
- **22 pairs are type-separable** — sample values resolve them cheaply
  (`Transaction Id` vs `Transaction Date`, `Loan A/c No` vs `Loan Amount`)
- **25 pairs are type-identical** — values are useless; only header semantics
  and position help (`First Name` vs `First Name_Plan`)
- 8 date fields, mutually indistinguishable by value

---

## 9. Minimum viable cut

Ship **S1 + S2 only — no AI**: normalization, the six family templates, an
alias dictionary, and a review loop where every confirmation feeds the
dictionary.

With 86% header reuse and six families covering 24 partners, this alone should
remove most manual effort — no model cost, no latency in the upload path, no
data-residency question. Add embeddings when the synonym tail (F5) becomes the
dominant review reason, and the LLM when entity ambiguity (F2) does.

**Sequence the AI by what the review queue is actually full of, not by what is
technically interesting.**
