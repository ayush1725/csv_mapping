# File Orientation and Layout Detection

Handling partner files that are not a simple one-policy-per-row grid.

**Status:** none of the 24 analysed partners send anything other than row-wise
data. This document specifies detection, which should be built now, and
handlers, most of which should be deferred until a real file requires them.

---

## 1. Shapes a partner file can take

### Shape 1 — Transposed

Fields down the first column, one policy per subsequent column.

```
Loan A/c No   | 12345      | 67890
First Name    | Ramesh     | Suresh
DOB           | 01/01/1990 | 02/02/1985
```

Typical origin: a report or pivot exported from a BI tool.

### Shape 2 — Key/value form

One policy per file, two columns.

```
Field,        Value
Loan A/c No,  12345
First Name,   Ramesh
```

Typical origin: a proposal form exported to CSV. Expected from small or
low-volume partners.

### Shape 3 — EAV / long

One row per field per policy, correlated by an identifier.

```
loan_ac, field_name,  value
12345,   First Name,  Ramesh
12345,   DOB,         01/01/1990
```

Typical origin: a database dump of an attribute table.

### Shape 4 — Row-wise with preamble

Normal orientation, but the header row is not row 0 — preceded by a title
block, logo row, blank rows, or merged cells. Often has trailing total or
footer rows.

**This is the most likely of the four to occur**, and the least exotic.

---

## 2. This is pre-processing, not a second pipeline

Once orientation is known, the file is normalized to row-wise and **everything
downstream is unchanged** — same family classification, same alias dictionary,
same LLM invocation policy, same validator.

```
file ──▶ [ ORIENTATION DETECT ] ──▶ normalize to row-wise ──▶ existing pipeline
                  │
                  └── undetermined ──▶ review queue, with a specific reason
```

The addition is one step in front of Layer 0, not a parallel design.

---

## 3. Detection

### 3.1 Primary signal — resolve both axes

Detection reuses the header resolver already being built. Run it against the
first row and the first column and compare hit rates.

```
R = fraction of row 0 cells resolving to a canonical field at >= threshold
C = fraction of column 0 cells resolving to a canonical field at >= threshold
```

| R | C | Conclusion |
|---|---|---|
| high | low | **Row-wise** — normal |
| low | high | **Transposed** — shape 1 or 2 |
| low | low | **Unknown** — run anchor search (§3.3) |
| high | high | **Ambiguous** — do not guess; route to review |

The `high/high` case is rare but real (a small matrix where both axes look like
field names). Guessing there risks transposing a valid file, so it must
escalate rather than resolve.

### 3.2 Confirming signal — type homogeneity

Independent of header text, and therefore a genuine second opinion.

In row-wise data a **column** is type-pure — a date column is all dates. In
transposed data a **row** is type-pure.

```
purity_cols = mean type-purity down each column
purity_rows = mean type-purity across each row

purity_cols > purity_rows  ->  row-wise
purity_rows > purity_cols  ->  transposed
```

**Decision rule:** act only when §3.1 and §3.2 agree. Disagreement routes to
review. Two independent signals agreeing is the bar for silently restructuring
a customer's file.

### 3.3 Anchor search — shape 4

When both hit rates are low, the header row may not be row 0. Scan the first
~10 rows and pick the row with the highest canonical hit rate; require it to
clear the same threshold.

Also detect and drop:

- Leading blank or merged-title rows
- Trailing total/footer rows (row where most numeric columns are sums, or the
  first cell reads `Total`, `Grand Total`, etc.)
- Fully blank separator rows

### 3.4 EAV detection — shape 3

Distinct enough to test directly:

- Total width is 2–4 columns, **and**
- One column's distinct values largely resolve to canonical field names, **and**
- Those values repeat many times across rows

If width is exactly 2 and field names occur once each, it is shape 2 (single
policy form) rather than shape 3.

---

## 4. Handling

| Shape | Transformation | Notes |
|---|---|---|
| 1 Transposed | Transpose the matrix; first column becomes the header row | Straightforward once detected |
| 2 Key/value form | Transpose; yields exactly one data row | Watch for duplicate field names — a repeated key usually means a repeating group |
| 3 EAV | Pivot on the identifier column | Requires a correlation key; `Loan A/c No` is present in 23/24 partners |
| 4 Preamble | Drop rows above the anchor and below the data block | Most common; also the cheapest |

After transformation the file re-enters the standard pipeline. The **original
layout is recorded in the audit log** so a mapping decision can always be traced
back to the file as the partner actually sent it.

---

## 5. What to build now, and what to defer

Zero of the 24 analysed partners send non-row-wise data, so handlers are
speculative. Detection is not.

| Component | When | Rationale |
|---|---|---|
| **Orientation detector + clear rejection message** | **Now** | Safety — see §6 |
| **Anchor search / preamble handling (shape 4)** | **Now** | Title rows and blank leading rows are near-certain in practice |
| Transpose handler (shapes 1–2) | On first real occurrence | Small change once detection exists |
| EAV pivot (shape 3) | Only if observed | Needs correlation-key rules |

**Design constraint to honour now:** the pipeline's entry point should accept a
*normalized row-wise table* produced by a detection step, rather than reading
the raw file directly. That interface makes adding a handler later a small
change rather than a restructuring.

---

## 6. Why detection matters even with no handler

Without an orientation check, a transposed file is read with row 0 as its
header row — which is actually policy data. Two outcomes:

| Outcome | Severity |
|---|---|
| Nothing resolves; the file goes to review with a confusing error | Wasted time |
| **A few cells coincidentally match canonical fields, and the file partially processes** | **Garbage policies created from misread data** |

The second is the real hazard, and it is the same class of failure as the
entity-ambiguity problem: the system proceeding confidently on a
misunderstanding.

A detector converts both outcomes into one clear, specific message —
*"this file appears to be transposed and cannot be processed"* — which is
actionable by operations and safe by default. **That value exists before any
handler is written.**

---

## 7. Effect on other decisions

| Area | Effect |
|---|---|
| Required-field gating | Applies after normalization, not to the raw file |
| Family classification | Runs on the normalized table; a transposed file may still belong to a known family |
| Row limit / SQS chunking | Chunking applies to normalized rows; a transposed file's policy count is its **column** count |
| Audit log | Must record original layout and the transformation applied |
| Review queue | Needs a distinct reason code: `layout_undetermined`, separate from `header_unresolved` |
