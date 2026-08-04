# Header Resolution — Requirements & Business Flow Discovery

**Status:** Open. Implementation paused pending answers.
**Purpose:** Establish the actual requirement and business flow before further build.

This document exists because the prototype was built ahead of requirements. The
matching mechanics are sound and reusable, but everything about *where the
capability sits in the process* and *who acts on its output* is currently
assumption. This is the instrument for replacing those assumptions with facts.

---

## 1. What is actually established

Facts, not inference:

| # | Established | Source |
|---|---|---|
| 1 | A bulk upload portal exists for policy creation | Stated |
| 2 | A row limit is configurable; rows go to SQS; each row calls the create-policy API per row | Stated |
| 3 | That pipeline works reliably today | Stated |
| 4 | Partner CSVs sometimes arrive with headers that don't match the system's headers | Stated |
| 5 | Those mismatches are currently resolved **manually** | Stated |
| 6 | Manual resolution is considered unworkable at 1000+ partners | Stated |
| 7 | The canonical schema (`policy_v2`) has 58 fields | Provided master data |
| 8 | 17 of those 58 fields are person-attributes across 4 distinct entities | Derived from master data |

Everything below item 8 is open.

---

## 2. Assumptions currently baked into the prototype

Each of these was chosen by the implementer, not specified. Each needs a
verdict: **confirmed**, **rejected**, or **modified**.

| # | Assumption | Consequence if wrong | Verdict |
|---|---|---|---|
| A1 | Resolution runs **per upload**, at file-arrival time | If format is agreed once at partner onboarding, this is a configuration tool, not a runtime service — a fundamentally different product | ☐ |
| A2 | An **internal ops team** works a review queue | If no one owns review, "flag for human" is a dead end; design must be fully automatic or bounce to partner | ☐ |
| A3 | It sits **before SQS**, as a pre-flight gate | Post-queue or per-row placement changes latency budget and failure handling entirely | ☐ |
| A4 | A file with unresolved headers **waits for review** | Alternatives: reject whole file, or ingest good columns and quarantine the rest | ☐ |
| A5 | A missing **required** field is a hard stop | Partial ingestion may be normal, with gaps filled later | ☐ |
| A6 | The canonical schema is **stable** | Per-product or frequent schema change makes versioning central, not incidental | ☐ |
| A7 | Mapping is **1:1 column → field** | Splits/transforms (`full_name` → first + last) may be common enough to be core, not an edge case | ☐ |
| A8 | Auto-apply threshold of **92** is appropriate | The real threshold is a function of what a wrong mapping costs — currently unknown | ☐ |
| A9 | Header text + sample values are **sufficient evidence** | Column position, partner identity, or file naming may carry decisive signal | ☐ |
| A10 | This is worth solving **downstream** | If the root cause is an unenforced partner template, upstream validation may be the higher-leverage fix | ☐ |

---

## 3. AS-IS: the current process

**To be completed with the operations team.** Follow one real partner file end
to end, including a case where headers had to be fixed.

### 3.1 Trigger and arrival

- How does a file arrive? (portal upload / SFTP / email / API)
- Who initiates it — the partner, or an internal user on their behalf?
- Is there a schedule, or is it ad hoc?
- Is there a partner-facing template or spec they are supposed to follow?
  - If yes: **why do they deviate?** Can their source system emit the required
    format at all?

### 3.2 Detection of a header problem

- Who or what notices the mismatch first?
- At what point — upload, validation, queueing, or when rows start failing?
- What does the system currently do: reject, error, silently drop columns?
- Is the partner told, or is it handled internally?

### 3.3 The manual fix

**The most important section.** "Resolve the header issue manually" needs to
become a concrete sequence of actions.

- Who does it — role, team, how many people?
- What do they physically do?
  - Edit the CSV directly?
  - Configure a mapping in a screen?
  - Write a script / SQL?
  - Send it back to the partner to fix?
- How do they know what the correct mapping is? Prior knowledge, documentation,
  asking the partner?
- How long does one file take? Best case and worst case.
- Is the fix **remembered** for that partner's next file, or repeated each time?
- Where is it recorded, if anywhere?

### 3.4 After the fix

- Does the file re-enter the normal flow, or a separate path?
- Is anything logged for audit?
- Who verifies the fix was correct — and how would anyone find out if it wasn't?

### 3.5 Volume and effort today

| Metric | Value |
|---|---|
| Active partners today | |
| Target partner count | 1000+ |
| Uploads per day/week | |
| Typical rows per file | |
| % of files with header problems | |
| Person-hours/week on manual resolution | |
| Current backlog or delay caused | |

---

## 4. Decisions to be made

These determine the shape of the solution. Each needs an owner and an answer.

### D1 — When does mapping happen?

| Option | Implication |
|---|---|
| **Per upload** | Runtime service in the hot path; latency matters |
| **Once at partner onboarding**, reused thereafter | Configuration tool; resolution runs rarely, drift detection matters more |
| **Hybrid** — onboarding establishes a template, per-upload detects drift | Most likely fit; needs both paths built |

### D2 — Who resolves what the system can't?

| Option | Implication |
|---|---|
| Internal ops review queue | Needs a UI, an owner, and an SLA |
| Bounce back to the partner | Needs partner-facing messaging; slower but scales |
| Fully automatic, no human | Requires very high confidence; unsafe without measurement |
| Tiered — auto above threshold, ops below, partner for structural problems | Most robust; most to build |

### D3 — What happens to a file that can't be fully resolved?

| Option | Implication |
|---|---|
| Reject entire file | Simple; frustrating for large files |
| Hold pending review | Needs state management and a queue |
| Ingest resolvable columns, quarantine rest | Partial policies — is that even valid? |

### D4 — Where does it sit relative to SQS?

| Option | Implication |
|---|---|
| Pre-flight, before enqueue | Clean separation; existing pipeline untouched |
| Inside the consumer, per row | Wasteful — same headers resolved repeatedly |
| Separate service called by the portal | Enables reuse across claims/renewals |

### D5 — What does a wrong mapping cost?

Needed to set the auto-apply threshold, which is currently a guess.

- What happens downstream if a column maps to the wrong field?
- Is a wrongly-created policy detectable? By whom, after how long?
- Is it reversible, and at what cost — operational, financial, regulatory?
- Is there a compliance or audit obligation around mapping decisions?

### D6 — Scope beyond policy upload

- Do claims / renewals / endorsements / commission files have the same problem?
- If yes, is a shared service in scope now, or is policy-only the mandate?

---

## 5. Open questions by stakeholder

**Operations / bulk upload team**
- Sections 3.2–3.5 in full
- Which partners are worst, and what do their files look like?
- Are there existing manual mapping records we could learn from?

**Product / business owner**
- D1, D2, D3, D6
- Is the goal reducing effort, reducing turnaround time, or enabling partner growth?
- What does success look like in a number?

**Engineering / architecture**
- D4, plus current portal stack, database, deployment model
- Is there an existing validation layer this should integrate with?
- Approved LLM / embedding providers; AWS region and Bedrock model access

**Risk / compliance**
- D5
- Is automated mapping of policy data acceptable at all, and under what controls?
- Audit trail requirements for mapping decisions
- Constraints on sending policy-holder data to a model, and where it may run

---

## 6. What cannot be decided until this is answered

| Blocked item | Blocked by |
|---|---|
| Auto-apply threshold | D5 — cost of a wrong mapping |
| Whether a review UI is needed at all | D2 |
| Service vs. embedded library | D4, D6 |
| Synchronous vs. asynchronous resolution | D1, plus volume/SLA |
| Whether Layer 4 (LLM) belongs in the hot path | D1, latency budget, D5 |
| Whether Bedrock is required | Compliance answer on data residency |
| What the eval set should contain | Real historical mappings from 3.3 |

---

## 7. Recommended next step

One working session with operations, walking a single real partner file from
arrival through to policy creation — including a case that needed manual header
fixing. That single walkthrough answers most of section 3 and several of the
decisions in section 4.

Bring: a real file that had header problems, and whoever actually fixed it.

---

## 8. Note on the existing prototype

The code in this repository is **not committed to a design**. It should be read
as a feasibility probe, and it did establish some genuinely useful facts about
the schema itself:

- 58 canonical fields; small enough that no vector database is needed
- 47 confusable field pairs, 12 of them across different people
- 4 distinct entities share one row, and unqualified person-attributes fail
  *systematically* rather than randomly
- Deterministic matching alone: 89.5% precision, 46.3% coverage, 0 wrong
  answers on a 41-case synthetic set

Those findings hold regardless of the business flow, because they are
properties of the master data. What is provisional is the orchestration around
them — placement, thresholds, review model, and service boundary. All of it is
cheap to rework once sections 3 and 4 are answered.
