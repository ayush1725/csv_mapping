# Edge Cases

Catalogue of failure modes for partner CSV ingestion, ordered by **how silently
they fail** rather than how likely they are. A loud failure costs time; a silent
one creates a wrong policy nobody notices until a claim.

Evidence cited is from the 24 partner samples in `data/partner_samples.json`.

---

## Class A — Silent corruption

**The dangerous class.** Data is wrong, every mapping is high-confidence, and no
error is raised.

### A1 — Excel strips leading zeros

The single highest-impact issue in practice. Opening a CSV in Excel and
re-saving it converts text to numbers:

| Field | Sent | Received |
|---|---|---|
| Pin Code | `0110 05` | `11005` |
| Mobile No | `09876543210` | `9876543210` |
| Loan A/c No | `0001234567` | `1234567` |
| Customer A/c No | `00456789` | `456789` |

Header mapping succeeds perfectly. The **value** is wrong. A truncated account
number may still match a real account.

**Control:** validate value *shape* per field after mapping — pin code is 6
digits, mobile is 10, account numbers have partner-specific lengths. A shape
violation on a mapped field is a hard stop, not a warning.

### A2 — Mobile numbers in scientific notation

Excel renders long numerics as `9.87654E+09`. Round-tripping loses precision
permanently. Same detection as A1; the mapping is fine, the value is destroyed.

### A3 — Date format ambiguity (DD/MM vs MM/DD)

`01/02/2024` is 1 February or 2 January depending on locale. **Undetectable from
a single value.** A partner exporting with US locale settings silently shifts
every ambiguous date.

**8 of our 58 fields are dates**, including `DOB` — which drives age, premium
and eligibility.

**Control:** infer the format across the **whole column**, not per value. Any
value with a day component >12 disambiguates the entire column. If a column is
*entirely* ambiguous (all values ≤12/12), it must go to review — never assume.
Record the inferred format in the audit log.

### A4 — Excel serial dates

Dates arriving as `45321` instead of `2024-01-15`. Detectable — a date-typed
column containing 5-digit integers in the 20000–60000 range is almost certainly
serial. Convert with the epoch stated explicitly, and beware the 1900 leap-year
bug.

### A5 — Indian digit grouping

`1,25,000` (lakh grouping) vs `125,000`. A naive parser splitting on commas or
stripping them inconsistently yields `125000` or `12500`. Currency symbols
(`₹`), trailing `/-`, and spaces compound it.

### A6 — PED and PEP are one character apart

| | |
|---|---|
| **PED** | Pre-Existing Disease — medical history |
| **PEP** | Politically Exposed Person — AML/compliance |

Fuzzy similarity **66.7**, and both appear as bare 3-letter headers in our
samples: MPGB sends `PED`, GGB sends `PEP`, several partners send both.
Completely unrelated meanings; mapping one to the other is both an underwriting
error and a compliance failure.

**Control:** add to the guard list explicitly as a never-auto-apply pair. Do not
rely on the derived guard list to catch it — 66.7 sits below typical thresholds,
so it would not be flagged as confusable at all.

`UTR` vs `UTRN` scores **85.7** and is a similar case.

### A7 — Source columns swapped, headers correct

Partner's export puts last name under `First Name`. Headers map perfectly;
values are transposed. Detectable only by value-shape checks (a date under a
name field) or downstream anomaly detection — **not** by header resolution.

Worth stating explicitly as **out of scope**, so nobody assumes the mapper
catches it.

### A8 — Wrong family classification

The highest-blast-radius failure in the proposed design. If a file is classified
into the wrong family, an entire verified template is applied wholesale and
**every** column maps confidently and wrongly.

**Control:** family classification must have its own confidence threshold and
must be **verified against the resolved header set**, not just similarity. If
the chosen family's template leaves more than X% of columns unresolved, reject
the classification and fall back to per-column resolution.

### A9 — Mapped but empty

A required field maps at 1.00 confidence to a column that is **entirely null**.
Mapping succeeded; the data does not exist. Every confidence metric looks
perfect.

**Control:** required-field validation must check *populated*, not *mapped*.

### A10 — Duplicate headers within one file

**7 of 24 partners** have repeated column names in a single file:

```
MPGB, GGB                 Title ×3, First Name ×3, Last Name ×3, DOB ×3, Customer ID ×2
Hello ujjivan standalone  Gender ×2, Insured DOB ×2
Uss                       Gender ×2
Rbl, House loader         emi_amount ×2
Mathooth                  memberId ×2
```

Naive parsers (including pandas without `mangle_dupe_cols`, and most
dict-based readers) **silently keep only the last occurrence**. For MPGB that
means the proposer's and insured's names are discarded and only the nominee's
survive.

**Control:** parse headers positionally into a list, never a dict. Covered by
the `occurrence_index` key in `dictionary-design.md`.

Note `Hello ujjivan standalone` sends `Insured DOB` twice — for Insured 1 and
Insured 2 — with no ordinal marker. Same class as the MPGB problem, applied to
members rather than roles.

---

## Class B — Structural and parse failures

Loud, mostly. Cheap to handle, expensive to discover in production.

### B1 — Two-row / grouped headers

Very common in insurance templates: a merged group row above the real header.

```
row 0:            |  Proposer      |          |     |  Insured       |
row 1:  Loan A/c  |  First Name    | Last Name| DOB |  First Name    | ...
```

**This is the clean solution to the MPGB ambiguity when it is present** — the
group row states the entity explicitly. Worth detecting and using as a
first-class signal, not just tolerating.

Merged cells appear as a value in the first column and blanks after, so the
group label must be forward-filled.

### B2 — Delimiter variation

Comma, semicolon, tab, pipe. **Semicolon is the Excel default in several
regional locales**, so a partner saving "CSV" may legitimately produce
semicolon-delimited output. Sniff the delimiter; do not assume comma.

### B3 — Encoding

UTF-8 BOM (`﻿` prefixed to the first header — silently corrupts the first
column name), UTF-16 from Excel's "Unicode Text" export, Latin-1/CP1252 for
files with `₹` or accented characters. Detect and normalize; strip BOM
explicitly.

### B4 — Commas and newlines inside header cells

**4 partners** use the hazardous-occupation declaration as a column header. It
contains commas and, in the source, line breaks:

> `Does your job require handling Hazardous Material or working at significant
> heights or high voltage or adventure sports, merchant navy & armed forces.`

Requires a real CSV parser with quote handling. A `split(',')` implementation
breaks the header row into fragments and misaligns every subsequent column.

### B5 — Multiple sheets in XLSX

Which sheet holds the data? Common patterns: a `Instructions` or `Master` sheet
first, data second; or one sheet per branch. Choose by locating the sheet whose
header row best resolves against the schema — reusing the orientation detector.

### B6 — Blank and unnamed columns

`Unnamed: 14`, empty string headers, `col_17`. Produced by merged cells, trailing
formatting, or deleted columns. An empty column with populated data underneath
is a real column with a lost name — send it to the LLM with sample values. An
empty column with no data is padding — drop it.

### B7 — Trailing junk rows

Total rows, `Grand Total`, signature blocks, disclaimers below the data. Detect
by shape: the required-field columns become null while one or two columns hold
aggregates.

### B8 — Empty file or headers only

Zero data rows. Should resolve headers successfully and then report "no rows" —
distinct from a mapping failure.

### B9 — Wrong file type

`.csv` that is actually XLS, XLSX, ODS, or HTML (Excel's "Save as web page").
Sniff magic bytes rather than trusting the extension.

---

## Class C — Genuine ambiguity requiring a human

### C1 — Opaque short headers

Our samples contain many headers that are uninterpretable in isolation:

```
DATE · AMT · LOT · GWP · EPP · PASI · CIF · DOI · UTR · UTRN · GMC · CIPA
Loan · Plan · Zone · EMI · PED · PEP
```

`DATE` against 8 date fields and `AMT` against 5 decimal fields are unresolvable
by header text alone. `Plan` appears in 7 partners and could mean plan name,
product plan option, or master policy.

These are exactly the cases for tool-assisted LLM inspection (see
`llm-design.md` §7.6), and the cases most likely to need a permanent
partner-scoped alias rather than a global one.

### C2 — Columns with no values to type-check

A column that is entirely empty in the sample rows gives no type signal. Sample
more rows before escalating; if still empty, it cannot be validated and must not
auto-apply to a required field (see A9).

### C3 — One partner, several formats

**`Hello ujjivan standalone` and `Hello ujjivan feeder` are the same partner
with two different formats** — 0.62 similarity, different families.

This breaks "pin one template per partner". Templates must be keyed by
**(partner, channel/product/source)**, not partner alone. Format identity must
be detected per file, not looked up by partner ID.

Note also that `Data loader` and `House loader` appear to be internal upload
tools rather than partner names — worth confirming, because it affects whether
they should be modelled as partners at all.

### C4 — Genuinely new business field

A partner sends something the schema has no concept of. Correct behaviour is
abstain and report, not force a nearest match. Recurring instances are input to
schema evolution (see `project-plan.md` D6).

---

## Class D — Business and semantic

### D1 — Proposer is also the insured

Self-cover. The same name, DOB and ID appear in both blocks. Legitimate, but
must not be mistaken for a duplicate-member error, and complicates the
member-count arithmetic (see `member-cardinality.md` Q7).

### D2 — Nominee is a minor

Guardian becomes **conditionally required**. Our schema has guardian fields but
no stated rule. Requires: if nominee age < 18, guardian fields must be present.

### D3 — Duplicate member within one policy

The same person appearing twice among the ≤6 members. Detect on (name, DOB) or
ID.

### D4 — Implausible values that still pass type checks

Future DOB, age > 100 or < 0, policy end before start, sum insured ≤ 0, loan
disbursal after application date. All type-valid, all wrong.

### D5 — Duplicate loan account across rows

Ambiguous between three cases: a multi-row policy (long format), a genuine
second policy on the same loan, or an accidental duplicate row. Cannot be
resolved without the answer to the wide-vs-long question in
`row-identity-analysis.md`.

### D6 — Same file uploaded twice

Needs an idempotency key. Serial numbers are unsuitable (they identify a line,
not a policy); use a content hash plus a business key. Related: a *corrected*
re-upload where some rows already created policies.

---

## Class E — Operational

### E1 — Partner format drift

A partner adds, removes or reorders columns without notice. This is the case the
hybrid design targets: the pinned template detects the drift, and only the delta
goes to review.

### E2 — Test or dummy data reaching production

Names like `test`, `ABC`, `Ramesh Kumar` repeated across every row, DOB
`01/01/1900`, sum insured `1`. Cheap heuristics; worth having because these
create real policies.

### E3 — Concurrent and partial re-uploads

Two uploads from the same partner in flight, or a re-upload after partial
processing. Needs row-level idempotency, not just file-level.

### E4 — Schema or dictionary version change mid-flight

A file resolved under schema v2 but processed after a v3 deploy; or an alias
corrected between resolution and enqueue. **Pin the schema version and
dictionary version into the decision log at resolution time**, and process the
file under the pinned versions.

### E5 — File uploaded under the wrong partner account

Family classification will disagree with the expected partner template. Treat a
strong mismatch as a signal, not just a mapping failure — it may indicate a
misrouted file.

---

## Priority

If only five controls are built, build these:

| # | Control | Prevents |
|---|---|---|
| 1 | **Positional header parsing** (never dict-keyed) | A10 — silent loss of duplicate columns, affects 7 of 24 partners |
| 2 | **Value-shape validation on mapped fields** | A1, A2, A5 — Excel corruption of pin codes, mobiles, account numbers |
| 3 | **Column-wide date format inference** | A3 — silently shifted dates including DOB |
| 4 | **Family classification confidence gate** | A8 — wholesale mis-mapping, highest blast radius |
| 5 | **Populated-not-just-mapped required check** | A9 — required field mapped to an empty column |

Items 1 and 2 are cheap and prevent the two most likely silent corruptions.

---

## Explicitly out of scope

State these so nobody assumes coverage:

- **Value-level correctness** — a correctly mapped column containing wrong data
  (A7) is not detectable by header resolution
- **Fraud** — deliberately falsified partner data
- **Business rule validation** beyond shape and cardinality — eligibility,
  premium calculation, underwriting rules remain with the create-policy API
