# Synthetic Document Corpus

> **Synthetic documents only.** Nothing here is derived from a real customer, a real
> account, or a real bank statement. No real financial data is used at any stage of this
> project.

The corpus is the ground truth the whole pipeline is measured against. If a document does
not actually contain the defect its manifest claims, then a validation engine that
"passes" against it has proven nothing.

## Build

```bash
python -m corpus.generator.build --as-of 2026-09-12 --seed 20260912
```

| Flag | Default | Purpose |
|---|---|---|
| `--as-of` | today | Reference date every period is derived from |
| `--seed` | `20260912` | Makes generation reproducible |
| `--out` | `corpus/generated/` | Output directory (git-ignored) |

Takes about 10 seconds and produces 19 documents plus `manifest.json`. The test suite
builds it automatically if it is missing, so a clean clone needs no manual step.

**Periods are derived from `--as-of`, never hardcoded.** A corpus with fixed dates would
quietly go stale and make the recency rule (R-PER-002) untestable a few months later.

## What is in it

| Scenario | Documents | Expected |
|---|---|---|
| 1 — Valid | `valid_{sbi,hdfc,icici,axis,kotak}` | PASS |
| 2 — Wrong period | `wrong_period_short_hdfc` (4 months) | FIX `PERIOD_INSUFFICIENT_COVERAGE` |
| | `wrong_period_stale_sbi` (6 months, ended 6 months ago) | FIX `PERIOD_STALE` |
| 3 — Password | `password_protected_icici` (AES-256) | gate → PASS |
| 4 — Wrong document | `wrong_document_gst_certificate` | FIX `DOC_TYPE_MISMATCH` |
| 5 — Corrupt / unusable | `corrupt_truncated_axis` | FIX `FILE_CORRUPT` |
| | `invalid_not_a_pdf` (text renamed `.pdf`) | FIX `FILE_UNSUPPORTED_TYPE` |
| | `invalid_empty` (zero bytes) | FIX `FILE_EMPTY` |
| 6 — Incomplete | `incomplete_missing_pages_axis` (pages 3–4 removed) | FIX `COMPLETENESS_MISSING_PAGES` |
| | `balance_break_icici` (one balance edited) | **REVIEW** `COMPLETENESS_BALANCE_BREAK` |
| 7 — Low confidence | `scanned_poor_kotak` (degraded photo) | REVIEW `EXTRACTION_LOW_CONFIDENCE` |
| | `scanned_clean_hdfc` (clean 300 dpi scan) | PASS — *positive control* |
| 8 — Account type | `wrong_account_type_savings_hdfc` | FIX `ACCOUNT_TYPE_NOT_CURRENT` |
| 9 — Identity | `identity_mismatch_axis` | REVIEW `IDENTITY_MISMATCH` |
| 10 — Integrity | `integrity_edited_producer_sbi` | PASS — *negative control* |

### The two controls matter

`scanned_clean_hdfc` and `integrity_edited_producer_sbi` are deliberately expected to
**PASS**. Without them the corpus could not distinguish a system that reasons from one
that is merely suspicious of anything unusual:

- The clean scan proves low confidence tracks **image quality**, not merely the absence of
  a text layer.
- The edited-producer document fires exactly **one** integrity signal, below the
  escalation threshold of 2, so it must still PASS. It proves weak signals do not flood
  Operations with noise.

## Design constraints

**Arithmetic consistency.** Every balance satisfies
`balance[i] = balance[i-1] + credit - debit`, exactly, in integer paise. Floats are never
used for money. This is what makes the balance-continuity validator (ADR-007) a real test
rather than a detector of the generator's own rounding noise. `Statement.assert_consistent()`
enforces it at build time.

**One defect per document.** A document with several overlapping problems would only tell
us the pipeline reached *a* verdict, not the right one for the right reason.

**Structural diversity across layouts.** The five profiles span every variation axis the
research identified: date formats (`%d %b %Y`, `%d/%m/%y`, `%d-%m-%Y`), column labels
(Narration / Particulars / Transaction Remarks / Description), separate Debit/Credit
columns vs. a single amount with a `Dr`/`Cr` marker, per-page balance bands, closing
summary blocks, and multi-line narration.

Two absences are deliberate: **Kotak prints no account type and no "Page X of Y" footer**,
so the corpus contains a valid statement where `R-DOC-002` and `R-CMP-001` are forced to
report `NOT_EVALUATED` rather than always finding their field.

**Bank profiles are not claims of fidelity.** `docs/RESEARCH_BANK_FORMATS.md` §4 is tagged
**UNVERIFIED** — no public source publishes per-bank column specifications. The profiles
exist so the extractor cannot be accidentally tuned to a single layout. No blocking
validation rule depends on any bank-specific detail.

## Two findings from building it

**1. "Corrupt" is a property of a parser, not of a file.** Truncating a PDF does not
reliably make it unopenable. Parsers rebuild damaged cross-reference tables by scanning
for objects, so recovery depends on where object boundaries happen to fall — and it is
**not monotonic** in how much of the file survives. It also differs between parsers: in
testing, PyMuPDF recovered truncations that pypdf rejected outright.

A hand-tuned truncation fraction would silently stop working the next time the corpus was
regenerated at a different size. So `corrupt_file()` keeps the `%PDF-` signature — the file
must reach the *parse* check (R-FILE-003), not be bounced by the *type* check (R-FILE-001)
— and replaces the body with deterministic noise. The generator then **asserts at build
time** that both parsers reject it.

**2. Keyword matching cannot classify a document.** The GST certificate contains
"Particulars of Approving Authority" — and "Particulars" is an Axis Bank transaction-table
column label. A classifier keying on single terms would call a GST certificate a bank
statement. R-DOC-001 must reason about table *structure*.

## Manifest schema

```jsonc
{
  "schema_version": 1,
  "as_of_date": "2026-09-12",
  "seed": 20260912,
  "application_business_name": "Sharma Metal Works Private Limited",
  "valid_period": { "start": "...", "end": "...", "coverage_days": 183 },
  "documents": [
    {
      "id": "valid_hdfc",
      "file": "valid_hdfc.pdf",
      "bytes": 38214,
      "sha256": "...",
      "scenario": "1_valid_statement",
      "defect": null,                    // null for clean documents
      "text_layer": "native",            // native | image
      "expected": {
        "outcome": "PASS",               // PASS | FIX | REVIEW
        "primary_reason_code": null
      },
      "application": {
        "external_reference": "FLX-...",
        "business_name": "..."           // what identity checks compare against
      },
      "password": null,                  // set only for the encrypted document
      "ground_truth": {
        "bank_name": "HDFC Bank Ltd",
        "account_holder_name": "...",
        "account_number_masked": "XXXXXXXXXX3926",
        "account_number_last4": "3926",
        "account_type": "CURRENT",       // null where the layout omits it
        "period_start": "2026-03-01",
        "period_end": "2026-08-31",
        "coverage_days": 183,
        "page_count": 7,
        "transaction_count": 436,
        "opening_balance_paise": 48500000,
        "closing_balance_paise": 121426824
      },
      "notes": "..."
    }
  ]
}
```

`ground_truth` is what Phase 6 asserts extraction accuracy against; `expected` is what
Phase 7 asserts verdicts against.

**Account numbers are masked in the manifest.** The full number exists only inside the
document itself — the same rule the application follows for every UI, API response, log
and export. A test asserts this.

## Module layout

| Module | Responsibility |
|---|---|
| `money.py` | Integer-paise arithmetic, Indian digit grouping (`1,23,456.78`) |
| `ledger.py` | Transaction generation with exact running balances |
| `profiles.py` | The five bank layout profiles |
| `render.py` | ReportLab rendering; GST certificate |
| `degrade.py` | Defect injection: encrypt, remove pages, corrupt, rasterise, break balance, rewrite metadata |
| `build.py` | Assembles the corpus and writes the manifest |
