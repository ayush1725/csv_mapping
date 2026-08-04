# Alias Dictionary — Data Model

Companion to `project-plan.md` and `llm-design.md`. Refines the proposed
"master header as key, partner headers as values" storage into a model that
survives what the partner samples actually contain.

---

## 1. The proposal and what it gets right

> Store the mapping of master policy headers as a key, then all similar
> partner headers as values. New partner headers are written back after
> validation.

The core is correct and is the mechanism that makes 1000+ partners affordable:
a learned dictionary means the free layer covers more traffic over time, and
call volume decays instead of growing with partner count.

Four properties of the real data break the flat form, and each has a
straightforward fix.

---

## 2. Why a flat `alias -> field` map is insufficient

### 2.1 The same header string has three different targets in one file

MPGB (and GGB) repeat bare, unmarked person blocks. Actual column positions:

```
  15 Customer ID     16 Title   17 First Name   18 Last Name   19 DOB    <- proposer
  ...
  33 Customer ID     35 Title   36 First Name   37 Last Name   38 DOB    <- insured
  ...
                     41 Title   42 First Name   43 Last Name   45 DOB    <- nominee
```

`First Name` occurs at 17, 36 and 42 and must map to `First Name`,
`First Name_Plan` and `Nominee First Name` respectively. Identically for `DOB`
(19/38/45) and `Title` (16/35/41).

A flat dictionary learns `First Name -> First Name` and then maps the insured
and the nominee onto the proposer on every MPGB file. This is the
wrong-person-insured failure, produced by the dictionary itself.

**Fix:** the key includes an occurrence index (§3.2).

### 2.2 One source column can have several targets

`Nominee Name` arrives as a single column in 9 of 24 partners, against
`Nominee First Name` + `Nominee Last Name`. Also `name` (Namra, Mathooth),
`custname` (Rbl), `NOMINEE_NAME` (L&T).

**Fix:** the value is a mapping spec with a target list and a transform (§3.3).

### 2.3 The same alias means different things to different partners

`Tenure` is a single canonical field, but partners send `Loan Tenure` and
`Policy Tenure` as distinct columns — a bare `Tenure` means different things
per partner. Likewise `Amount` against `Actual Premium` / `Transaction Amount`.
Across products, `amount` is premium in policy and claim amount in claims.

**Fix:** scoped dictionaries with most-specific-wins lookup (§4).

### 2.4 Writing every auto-applied mapping back makes errors permanent

If a 0.90-confidence LLM guess is persisted as fact, the dictionary teaches
itself the mistake and every later file inherits it. The learning loop
amplifies its own errors.

**Fix:** provisional/active/negative lifecycle (§5).

---

## 3. Storage model

### 3.1 Direction

`master_field -> [aliases]` is the right mental model but the wrong index. At
resolution time the input is a partner header and the output is a master field,
so rows are keyed on the alias. Render the `master -> [aliases]` grouping as a
**view** for the operations UI.

### 3.2 Key

```
(tenant_id, schema_id, scope, scope_ref, alias_norm, occurrence_index)
```

| Component | Purpose |
|---|---|
| `tenant_id` | multi-tenant isolation |
| `schema_id` | `loan_v2` / `health_v1` / `travel_v1` — prevents cross-product poisoning |
| `scope`, `scope_ref` | `global` / `family:<id>` / `partner:<id>` (§4) |
| `alias_norm` | normalized header (§6) |
| `occurrence_index` | 0-based nth occurrence of this header in the file; solves §2.1 |

### 3.3 Value

```json
{
  "targets": ["Nominee First Name", "Nominee Last Name"],
  "transform": "split_name",
  "entity": "nominee",
  "confidence": 0.94,
  "position_hint": 41
}
```

`targets` is a list — one entry for the common case, more for splits.
`transform` is drawn from a closed set (`identity`, `split_name`, `constant`,
`derive`); unknown transforms are rejected by the validator.

### 3.4 Schema sketch

```sql
CREATE TABLE header_alias (
  id                BIGSERIAL PRIMARY KEY,
  tenant_id         TEXT NOT NULL,
  schema_id         TEXT NOT NULL,
  scope             TEXT NOT NULL,          -- global | family | partner
  scope_ref         TEXT,                   -- NULL for global
  alias_raw         TEXT NOT NULL,          -- exactly as received
  alias_norm        TEXT NOT NULL,          -- normalized lookup key
  occurrence_index  INT  NOT NULL DEFAULT 0,
  targets           TEXT[] NOT NULL,
  transform         TEXT NOT NULL DEFAULT 'identity',
  entity            TEXT,
  position_hint     INT,
  status            TEXT NOT NULL,          -- provisional | active | negative | retired
  confidence        NUMERIC(4,3),
  source_layer      TEXT,                   -- alias | fuzzy | embedding | llm | human
  times_seen        INT NOT NULL DEFAULT 1,
  clean_files       INT NOT NULL DEFAULT 0, -- files applied without correction
  first_seen_at     TIMESTAMPTZ NOT NULL,
  last_seen_at      TIMESTAMPTZ NOT NULL,
  confirmed_by      TEXT,
  confirmed_at      TIMESTAMPTZ,
  UNIQUE (tenant_id, schema_id, scope, scope_ref, alias_norm, occurrence_index)
);

CREATE INDEX ON header_alias (tenant_id, schema_id, alias_norm) WHERE status = 'active';
```

An append-only decision log is kept **separate** from the dictionary — the
dictionary is current state, the log is history:

```sql
CREATE TABLE mapping_decision (
  id            BIGSERIAL PRIMARY KEY,
  file_id       TEXT NOT NULL,
  partner_id    TEXT NOT NULL,
  column_index  INT  NOT NULL,
  header_raw    TEXT NOT NULL,
  targets       TEXT[],
  transform     TEXT,
  confidence    NUMERIC(4,3),
  resolved_by   TEXT NOT NULL,   -- which layer
  alias_id      BIGINT,          -- dictionary row used, if any
  outcome       TEXT NOT NULL,   -- auto_applied | reviewed | corrected | rejected
  actor         TEXT,
  created_at    TIMESTAMPTZ NOT NULL
);
```

The log answers the question that matters after a bad mapping is discovered:
**which files used it?** Without it, a wrong alias cannot be remediated.

Layer 3 needs one more table so headers are embedded once, not per file:

```sql
CREATE TABLE header_embedding (
  alias_norm TEXT PRIMARY KEY,
  model      TEXT NOT NULL,
  vector     VECTOR(1024)
);
```

---

## 4. Three scopes, with promotion

A single flat dictionary poisons itself (§2.3). Three tiers, resolved
**most-specific-first**:

| Scope | Holds | Example |
|---|---|---|
| `global` | universally safe aliases | `DOB`, `Propsoal no`, `Aplication Date`, abbreviations |
| `family` | one of the ~6 template families | `Proposer/Applicant FIRST Name` -> `First Name` |
| `partner` | partner overrides and positional entries | MPGB `First Name` occurrence 1 -> `First Name_Plan` |

**Lookup order:** `partner` -> `family` -> `global`. First active hit wins.

**Write policy:** new mappings are always written at `partner` scope. Narrow
writes are cheap to correct; broad writes are expensive.

**Promotion:** a partner-scoped alias is promoted to `family`, and a
family-scoped alias to `global`, only when

- it appears in >= N distinct partners (start N=3), **and**
- it has zero contradicting entries elsewhere, **and**
- it is `active` (human-confirmed), **and**
- `occurrence_index = 0` and `position_hint IS NULL`

**Positional entries never promote.** MPGB's `First Name#1 -> First Name_Plan`
is true only for that column layout; promoting it would corrupt every other
partner. This rule is the single most important guard in the model.

Promotion should run as a periodic job producing a review list, not silently.

---

## 5. Lifecycle — the write path

```
new header
   |
   +-- resolved by L0-L3 above threshold ---> apply, bump times_seen
   |                                          (already in dictionary)
   |
   +-- resolved by LLM / embedding ---------> validator
   |                                             |
   |                          +------------------+------------------+
   |                          |                                     |
   |                    auto-applied                          sent to review
   |                    (>= threshold)                              |
   |                          |                              human decides
   |                    write PROVISIONAL                           |
   |                          |                        +------------+-----------+
   |                    N clean files                  |                        |
   |                          |                     confirm                  reject
   |                          v                        |                        |
   |                       ACTIVE  <--------------------                        v
   |                                                                        NEGATIVE
```

| Status | Meaning | Behaviour |
|---|---|---|
| `provisional` | applied without human confirmation | used, but re-verified; promotes to `active` after N clean files |
| `active` | human-confirmed | auto-applies |
| `negative` | human rejected this mapping | never suggested again |
| `retired` | superseded or schema changed | ignored, kept for audit |

Two points that are easy to get wrong:

**Negative entries are as valuable as positive ones.** Without them the model
re-proposes the same rejected mapping every month and ops re-rejects it every
month.

**Correction must propagate.** When a human corrects an alias, use
`mapping_decision` to list every file that used the old value and flag those
policies for review. A dictionary without this cannot recover from its own
mistakes.

---

## 6. Normalization for `alias_norm`

Applied before lookup and before storage:

1. Truncate at the first parenthetical — `Policy Tenure ( Cannot be less than
   Loan tenure...)` -> `Policy Tenure`. Without this, embedded business rules
   dominate the distance metric.
2. Cap length (~64 chars) — declaration paragraphs are used as headers in 7
   partners.
3. Lowercase; split camelCase and PascalCase; collapse separators
   (`_`, `-`, `/`, `.`) to spaces.
4. Expand domain abbreviations (`a/c` -> account, `DOB`, `PEP`, `RM`, `SI`).
5. Collapse whitespace.

**Do not over-normalize.** `Loan Tenure` and `Policy Tenure` must not collapse
to `tenure`; qualifiers carry the meaning. Normalization that strips leading
qualifiers is unsafe here.

Store `alias_raw` alongside `alias_norm` — the raw form is needed for audit and
for showing operations exactly what the partner sent.

---

## 7. What this buys

- MPGB's three identical `First Name` columns resolve correctly and are learned
  as three distinct entries
- `Nominee Name` learns as a split, not a lossy 1:1
- `Tenure` cannot mean Loan Tenure for one partner and Policy Tenure for
  another in the same table
- An LLM mistake is contained at partner scope and is correctable, with the
  affected files identifiable
- A rejected suggestion stays rejected
- Promotion turns repeated partner-level learning into family and global
  coverage, which is what makes onboarding partner 1000 cheap
