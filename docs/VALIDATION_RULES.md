# Validation Rules — 6-Month Current-Account Bank Statement

**Status:** v1.2 (Phase 1 rules, amended by the Phase 7 implementation and again by a
real submission the v1.1 rules judged wrongly — see ADR-026 … ADR-031)
**Derived from:** `RESEARCH_BANK_FORMATS.md`, `RESEARCH_REGULATORY.md`
**Implements:** the outcome contract in `PRODUCT_SPEC.md` §7

Every rule carries an evidence tag. **No rule exists because it makes the demo work.**

---

## 0. How rules work

A rule evaluates against the normalised extraction and returns one of four states:

| State | Meaning |
|---|---|
| `PASS` | The rule was evaluated and satisfied. |
| `FAIL` | The rule was evaluated and violated. |
| `NOT_EVALUATED` | The data the rule needs was not extractable. **Not a failure.** Reduces rule coverage, which lowers composite confidence, which pushes toward REVIEW. |
| `SIGNAL` | Non-blocking observation. Cannot cause FAIL; can contribute to REVIEW. |

Every result carries **evidence** — the actual values compared — so that both the customer
message and the Operations card can be generated from the rule output rather than
re-derived. A rule that cannot produce evidence is not fit for purpose.

**Blocking** rules can change the outcome. **Non-blocking** rules only inform confidence and
the Operations view.

All thresholds below live in `backend/app/config/validation_rules.yaml`. None are hardcoded.

---

## 1. File and ingestion rules

These run before extraction. They are cheap, certain, and universally applicable.

### R-FILE-001 — Supported file type
- **Blocking.** Tag: **ASSUMPTION** (a product scope decision).
- **Check:** file is a PDF, determined by **magic bytes** (`%PDF-`), not extension or
  client-supplied MIME type.
- **Config:** `allowed_mime_types: [application/pdf]`
- **On fail:** `FILE_UNSUPPORTED_TYPE` → **FIX**
- **Why magic bytes:** a client-supplied MIME type is attacker-controlled and a renamed
  extension is trivial. This is a security control, not a convenience check.
- **Note:** images (JPG/PNG photographs of statements) are deliberately **not** accepted in
  the MVP. A photo of a statement is a legitimate real-world submission and belongs in a
  later phase; accepting it now would widen the OCR problem without proving anything new.

### R-FILE-002 — File size within limits
- **Blocking.** Tag: **ASSUMPTION**.
- **Check:** `0 < size ≤ max_file_size_mb`
- **Config:** `max_file_size_mb: 20`
- **On fail:** `FILE_TOO_LARGE` → **FIX** (or `FILE_EMPTY` → **FIX** at zero bytes)
- **Rationale:** 6 months of current-account activity for an MSME is realistically 10–40
  pages of native-text PDF — comfortably under 20 MB. A file far above this is a scan at
  unnecessary resolution or not a statement at all. The cap is also a denial-of-service
  control.

### R-FILE-003 — File is a parseable PDF
- **Blocking.** Tag: **ASSUMPTION**.
- **Check:** the PDF structure opens and a page tree is readable.
- **On fail:** `FILE_CORRUPT` → **FIX** ("re-download from your bank and send again")
- **Distinction that matters:** *structurally corrupt* (this rule) is not the same as
  *unreadable content* (R-RED-001) and neither is the same as *invalid document*
  (R-DOC-001). These are three different customer messages and three different reason codes.
  Conflating them is the most common way document products give useless feedback.

### R-FILE-004 — Encryption detection
- **Not a verdict. A gate.** Tag: **VERIFIED** (Indian e-statements are commonly encrypted —
  `RESEARCH_BANK_FORMATS.md` §2).
- **Check:** is the PDF encrypted?
- **If yes:** transition to `AWAITING_PASSWORD`, emit `FILE_PASSWORD_REQUIRED`, prompt the
  customer. This is **not** a FIX — nothing is wrong with the document.
- **On wrong password:** `FILE_PASSWORD_INCORRECT` → re-prompt, rate-limited.
- **Config:** `max_password_attempts: 5`
- **Handling:** password in memory only; never logged, never persisted, never hashed;
  decrypted bytes never written to disk (`RESEARCH_REGULATORY.md` §3).

---

## 2. Document-type rules

### R-DOC-001 — Document is a bank statement
- **Blocking.** Tag: **VERIFIED** (the requirement is a bank statement).
- **Check:** structural classification — presence of a transaction table with recognisable
  date/amount/balance columns, an account header block, and statement-period markers.
  Scored, not binary.
- **Config:** `doc_type_confidence_threshold: 0.70`
- **On fail (confidently not a statement):** `DOC_TYPE_MISMATCH` → **FIX**, naming what was
  received where identifiable ("this looks like a GST registration certificate").
- **On ambiguity (score near threshold):** `DOC_TYPE_UNCERTAIN` → **REVIEW**.
- **Design note:** classification is **structural first**. An LLM may be consulted to name
  an unrecognised document for a friendlier message, but the *decision* comes from structure
  (ADR-001). Naming the wrong document is a message-quality improvement, not a verdict.

### R-DOC-002 — Account is a current account
- **Blocking, with an escape hatch.** Tag: **VERIFIED** (FlexiLoans requires a *current*
  account statement — `RESEARCH_BANK_FORMATS.md` §1).
- **Check:** account type stated in the header matches a current-account token
  (`current`, `current account`, `CA`, `CURRENT A/C`).
- **On fail (clearly savings):** `ACCOUNT_TYPE_NOT_CURRENT` → **FIX**
- **On absent/unreadable:** `ACCOUNT_TYPE_UNKNOWN` → `NOT_EVALUATED`, **not** a failure.
- **Config:** `account_type_absent_behaviour: not_evaluated`
- **Honest caveat (flagged in `PROGRESS.md`):** account type is **not guaranteed to be
  explicitly printed** on every Indian bank statement. Treating absence as failure would
  generate false FIX messages telling customers to send a document they already sent. So
  absence degrades coverage and leans toward REVIEW instead. This behaviour is config-driven
  precisely because it may need revisiting against real samples.

---

## 3. Period rules — the highest-value rules in the product

The period is the most common correctable failure and the clearest customer message.

### R-PER-001 — Sufficient coverage
- **Blocking.** Tag: **VERIFIED** (6 months of current-account statements is FlexiLoans'
  published requirement; 6–12 months is the market norm).
- **Check:** `(period_end − period_start) ≥ required_coverage_days`
- **Config:** `required_coverage_days: 180`, `coverage_tolerance_days: 3`
- **On fail:** `PERIOD_INSUFFICIENT_COVERAGE` → **FIX**, quoting the received period and the
  required period explicitly.
- **Why a tolerance:** a statement covering 1 Apr – 30 Sep is 182 days; 1 Mar – 31 Aug is
  183; but 15 Mar – 12 Sep is 181. Calendar-month boundaries do not divide evenly, and a
  customer who did exactly the right thing must not receive a FIX because of an arithmetic
  edge. The tolerance absorbs boundary effects, not genuinely short statements — a 4-month
  statement fails by ~60 days, nowhere near the tolerance.

### R-PER-002 — Statement is recent
- **Blocking.** Tag: **ASSUMPTION** — *this is the weakest-evidenced rule in the product and
  is flagged as such.*
- **Check:** `(reference_date − period_end) ≤ recency_tolerance_days`
- **Config:** `recency_tolerance_days: 35`, `reference_date_source: application_created_at`
- **On fail:** `PERIOD_STALE` → **FIX**
- **Honest basis:** research established the required *length* (6–12 months) but **no public
  source specifies how recent the end date must be** (`RESEARCH_BANK_FORMATS.md` §6). The
  35-day figure is our reasoning, not evidence: banks generate statements on monthly cycles,
  so the most recent statement available to a customer on any given day can legitimately be
  up to ~31 days old, plus a few days of slack. **This number should be the first thing
  calibrated against a real lender's policy.**
- **Why `application_created_at` and not "today":** using today's date would mean a document
  validated correctly on Monday could fail on re-validation weeks later, through no fault of
  the customer. Anchoring to the application makes the rule stable and reproducible — which
  the determinism requirement (`PRODUCT_SPEC.md` §11) demands.

### R-PER-003 — Period is determinable
- **Coverage rule.** Tag: **INDUSTRY** (period is always present in some form).
- **Check:** both `period_start` and `period_end` were extracted with confidence above
  threshold.
- **If not:** `PERIOD_NOT_FOUND` → `NOT_EVALUATED` → drives composite confidence down →
  **REVIEW**.
- **Never a FIX.** The customer cannot fix our inability to read a date.
- **Date-ambiguity guard:** dates are parsed **day-first** (Indian convention). Where a date
  is genuinely ambiguous and no unambiguous date elsewhere in the document resolves the
  format, the parser must report ambiguity rather than pick — a silently wrong month
  produces a confidently wrong verdict, the worst possible failure mode.

---

## 4. Completeness rules

### R-CMP-001 — Page continuity
- **Blocking.** Tag: **INDUSTRY** (missing pages are a named failure and fraud mode).
- **Check:** where a "Page X of Y" footer is present, all pages 1..Y are present and in order.
- **On fail:** `COMPLETENESS_MISSING_PAGES` → **FIX** ("pages 7–9 are missing; please send
  the complete statement").
- **If no footer pattern exists:** `NOT_EVALUATED` — footers are common but not universal.

### R-CMP-002 — Transaction-level balance continuity ★
- **Blocking.** Tag: **INDUSTRY** — explicitly named across bank-statement-analysis sources
  as a primary integrity check (`RESEARCH_BANK_FORMATS.md` §5).
- **Check:** for every consecutive transaction pair,
  `balance[i-1] + credit[i] − debit[i] == balance[i]` within a rounding tolerance.
- **Config:** `balance_tolerance_paise: 100` (₹1.00)
- **On fail:** `COMPLETENESS_BALANCE_BREAK` → **REVIEW**
- **Why REVIEW and not FIX — the important judgement:** a balance break has two
  indistinguishable causes: (a) pages or rows are missing, or (b) figures were edited after
  issuance. The arithmetic cannot tell them apart. Sending a FIX would tell an honest
  customer with a partial export something useful — and would tip off a dishonest one about
  exactly which number failed. Routing to a human is correct on both counts.
- **If balances are absent from the format:** `NOT_EVALUATED` — some layouts omit a running
  balance. Coverage drops accordingly.
- **Minimum evidence (added v1.2, ADR-026):** the rule is `NOT_EVALUATED` unless at least
  `min_transactions_for_balance_check: 10` *complete* transactions were parsed. A chain
  cannot be judged from a handful of rows, and breaks found in a partial read are artefacts
  of our extraction rather than defects in the customer's document. This was added after a
  real statement read at 6 transactions produced a confident `COMPLETENESS_BALANCE_BREAK`.

### R-CMP-003 — Page-boundary balance carry-over
- **Blocking.** Tag: **INDUSTRY**.
- **Check:** closing balance of page *N* equals opening balance of page *N+1*.
- **On fail:** `COMPLETENESS_MISSING_PAGES` → **FIX** when page numbering *also* shows a gap
  (two agreeing signals ⇒ confident, actionable); otherwise `COMPLETENESS_BALANCE_BREAK` →
  **REVIEW**.
- **Why the two-signal rule:** agreement between an independent structural signal (page
  numbers) and an arithmetic one (balances) is what converts an ambiguous anomaly into a
  confident, customer-actionable instruction. One signal alone is not enough to send someone
  off to re-upload.

### R-CMP-004 — Transactions span the claimed period
- **Blocking.** Tag: **INFERRED**.
- **Check:** the first and last transaction dates fall within the stated period, and the
  transaction date sequence is monotonic.
- **On fail:** `COMPLETENESS_MISSING_PAGES` → **FIX**
- **Catches:** a statement whose header claims six months while the table contains four —
  a header edit that the period rules alone would miss entirely.

### R-CMP-005 — Whole-table capture (added v1.2)
- **Blocking.** Tag: **ASSUMPTION** — derived from an observed real submission, not from a
  published source.
- **Check:** if a transaction table was found and rows were parsed, at least one row carries
  both an amount and a balance.
- **On fail:** `READABILITY_PARTIAL_CAPTURE` → **FIX** ("I can see your transactions and
  their dates, but the amount column is cut off — please send the statement PDF your bank
  issues").
- **Not composite-gated:** the finding is structural. We are not judging how well we read
  the document; we read it well enough to parse every date, and can see which column is
  absent.
- **Why this needs its own code:** without it, a screenshot of a banking app surfaces as a
  balance break (blaming a defect that does not exist) or a low-confidence review (true, but
  it strands a case with a human when the customer could resolve it in one message). The
  distinguishing evidence is that the dates parsed: the document is legible, and only
  *narrow*.

---

## 5. Readability rules

### R-RED-001 — Extractable text content
- **Blocking.** Tag: **ASSUMPTION**.
- **Check:** proportion of pages yielding usable text (native layer or OCR above quality
  threshold).
- **Config:** `min_readable_page_ratio: 0.90`, `min_ocr_word_confidence: 0.60`
- **On fail (no usable text anywhere):** `READABILITY_NO_TEXT` → **FIX** ("I can't read this
  copy — please send the PDF you received from your bank rather than a photo or scan").
- **On partial (some pages poor):** `READABILITY_POOR_SCAN` → **FIX** if the unreadable
  pages are identifiable and few; otherwise the confidence drop carries it to **REVIEW**.
- **Denominator (amended v1.2, ADR-027):** the ratio is over the pages *read*, not the pages
  in the document. How much of the document we got through is `page_coverage`, a separate
  number that already lowers confidence. Conflating them made "extraction stopped early" —
  our decision — indistinguishable from "this is a poor scan", and told a customer with a
  perfectly clear statement that their copy was unreadable.
- **Actionability test:** this is a FIX only because a concrete better action exists — the
  bank's original PDF. If no such action existed, it would be a REVIEW
  (`PRODUCT_SPEC.md` §7).

---

## 6. Integrity signals — non-blocking, REVIEW-only

**Tag: INDUSTRY** (all signals below are named in published bank-statement-fraud sources).
**Governing constraint (ADR-008): these can only escalate to REVIEW. They can never reject a
document and are never described to the customer as suspicion.**

| ID | Signal | Notes |
|---|---|---|
| R-INT-001 | PDF producer/creator metadata inconsistent with a bank-generated document (e.g. a consumer PDF editor) | Weak on its own — many customers legitimately re-save or split PDFs |
| R-INT-002 | Multiple incremental update revisions in the PDF structure | Indicates post-issuance modification of some kind, benign or not |
| R-INT-003 | Text layer is an image where a native layer would be expected, or font/rendering is inconsistent across pages | Weak; correlates with scanning as much as editing |
| R-INT-004 | A calendar month within the period contains zero transactions | Named as a fraud marker; also entirely legitimate for a dormant account |

**Escalation rule:** a single weak signal does **not** trigger REVIEW — that would flood
Operations with noise for no gain. REVIEW is raised when `integrity_signal_count ≥
integrity_review_threshold`, **or** when any integrity signal co-occurs with a
`COMPLETENESS_BALANCE_BREAK`.

- **Config:** `integrity_review_threshold: 2`
- **Reason code:** `INTEGRITY_SIGNALS_RAISED` → **REVIEW**
- **Operations sees:** which specific signals fired and their individual weakness. A human
  needs the evidence, not a score.

---

## 7. Identity consistency (limited, per MVP scope)

### R-IDN-001 — Account holder matches the application
- **Non-blocking → REVIEW only.** Tag: **ASSUMPTION**.
- **Check:** normalised comparison of `account_holder_name` against the application's
  business name — case-folded, punctuation- and legal-suffix-stripped
  (`Pvt Ltd`, `Private Limited`, `LLP`, `& Co`, `Enterprises`), then fuzzy-matched.
- **Config:** `identity_strong_match: 0.90`, `identity_clear_mismatch: 0.50`

| Score | Outcome |
|---|---|
| ≥ 0.90 | Contributes to PASS |
| 0.50 – 0.90 | `IDENTITY_AMBIGUOUS` → **REVIEW** |
| < 0.50 | `IDENTITY_MISMATCH` → **REVIEW** |

- **Why a clear mismatch is REVIEW and not FIX:** an MSME's registered legal name routinely
  differs from its bank account name, its trade name, and its proprietor's personal name —
  and a sole proprietor's current account may legitimately be in an individual's name.
  Automatically telling such a customer "this is the wrong account" would be wrong often
  enough to damage trust. A human resolves it in seconds. This is exactly the class of
  problem the REVIEW state exists for.
- **Scope boundary:** this is the *only* cross-document check in the MVP. The PAN ↔ GST ↔
  Bank engine is explicitly post-MVP (`PRODUCT_SPEC.md` §6).

---

## 8. Confidence model

**Tag: ASSUMPTION throughout.** These weights and thresholds are reasoned, not empirically
fitted. They are config-driven and are expected to be tuned against the corpus in Phase 7.

### 8.1 Field confidence

Each critical field carries an extraction confidence in `[0,1]`. The weighted field score:

| Field | Weight | Rationale |
|---|---|---|
| `period_start` | 0.20 | Drives the highest-value rules |
| `period_end` | 0.20 | Drives both period rules |
| `transactions[]` | 0.25 | Drives every completeness check |
| `account_holder_name` | 0.15 | Drives identity |
| `account_type` | 0.10 | Drives R-DOC-002 |
| `account_number` | 0.10 | Continuity and duplicate detection |

`field_score = Σ(weight_f × confidence_f)`

### 8.2 Rule coverage

`rule_coverage = (# blocking rules evaluated) / (# blocking rules enabled)`

A document where half the rules could not run is **not** high-confidence, however crisp the
OCR was. This term is what makes the score honest (ADR-009).

### 8.3 Composite

```
composite = 0.60 × field_score + 0.40 × rule_coverage
```

**Config:** `field_score_weight: 0.60`, `rule_coverage_weight: 0.40`

### 8.4 Thresholds — deliberately asymmetric

| Threshold | Value | Meaning |
|---|---|---|
| `pass_threshold` | **0.80** | PASS requires composite ≥ this |
| `fix_threshold` | **0.65** | Issuing a FIX requires composite ≥ this **and** the failing rule's own evidence confidence ≥ `rule_evidence_threshold` |
| `rule_evidence_threshold` | **0.75** | The specific rule's evidence must be solid before we send a customer to act on it |

**Why PASS demands more confidence than FIX.** The costs are asymmetric. A wrong PASS pushes
a bad document into downstream processing, where it is discovered late — *precisely the
failure this product exists to prevent*, and it destroys trust in every other PASS. A wrong
FIX costs the customer one unnecessary re-upload and an apology. Requiring more certainty to
say "this is fine" than to say "this specific thing is wrong" follows directly from that.

### 8.5 Outcome resolution *(as implemented)*

```
if a blocking rule FAILED:
    code = highest-priority failing code
    if code's default outcome is REVIEW, or it is not customer-actionable:
        -> REVIEW (code)
    if (code is not composite-gated  or  composite >= fix_threshold)
       and rule evidence >= rule_evidence_threshold:
        -> FIX (code)
    -> REVIEW (EXTRACTION_LOW_CONFIDENCE, downgraded_from = code)

elif integrity signals escalate:            -> REVIEW (INTEGRITY_SIGNALS_RAISED)
elif an advisory rule failed (identity):    -> REVIEW (that code)
elif composite >= pass_threshold:           -> PASS
else:                                       -> REVIEW (EXTRACTION_LOW_CONFIDENCE)
```

**Invariant, asserted in `tests/test_validation_engine.py`:** there is no path from a
low composite score or an unreadable document to `PASS`.

#### Two refinements the implementation forced

**`composite_gated` (ADR-018).** The composite score measures how well we read *a bank
statement*. For a document that is not one — a GST certificate — it is near zero
precisely *because there was no statement to read*, and gating on it would convert the
most confident verdict available ("this is not a bank statement") into a needless manual
review. File-level codes, `DOC_TYPE_MISMATCH`, `READABILITY_NO_TEXT` and
`EXTRACTION_LOW_CONFIDENCE` are therefore ungated.

**Downgrade reporting (ADR-019).** When a FIX is downgraded for low confidence, the
primary reason becomes `EXTRACTION_LOW_CONFIDENCE` rather than the rule that fired.
Reporting the rule would imply we know the document is at fault, when what we actually
know is that we could not read it well enough to decide — and it would file every
low-confidence case under whichever rule happened to fire first, distorting the
failure-reason distribution.

#### Readability gates document type

`R-DOC-001` reports `NOT_EVALUATED` whenever readability failed. If we could not read the
document, we cannot claim it is the *wrong* document — and saying so would send the
customer to fetch a statement they may already have sent.

### 8.6 Primary-reason priority

When several rules fail, the customer sees one issue (`PRODUCT_SPEC.md` §9). Priority order:

1. `FILE_*` — nothing else can be evaluated until the file is usable
2. `DOC_TYPE_MISMATCH` — wrong document makes every other rule moot
3. `ACCOUNT_TYPE_NOT_CURRENT`
4. `PERIOD_INSUFFICIENT_COVERAGE` / `PERIOD_STALE`
5. `COMPLETENESS_*`
6. `READABILITY_*`

The ordering is causal, not arbitrary: each level makes the levels below it unmeasurable.
Secondary failures are shown only when they share the same corrective action.

---

## 9. Configuration summary

```yaml
# backend/app/config/validation_rules.yaml
document_type: bank_statement

file:
  allowed_mime_types: [application/pdf]
  max_file_size_mb: 20
  max_password_attempts: 5

document:
  doc_type_confidence_threshold: 0.70
  account_type_absent_behaviour: not_evaluated

period:
  required_coverage_days: 180
  coverage_tolerance_days: 3
  recency_tolerance_days: 35          # ASSUMPTION — calibrate against lender policy
  reference_date_source: application_created_at

completeness:
  balance_tolerance_paise: 100

readability:
  min_readable_page_ratio: 0.90
  min_ocr_word_confidence: 0.60

integrity:
  integrity_review_threshold: 2

identity:
  identity_strong_match: 0.90
  identity_clear_mismatch: 0.50

confidence:
  field_score_weight: 0.60
  rule_coverage_weight: 0.40
  pass_threshold: 0.80
  fix_threshold: 0.65
  rule_evidence_threshold: 0.75
```

---

## 10. Rules deliberately not built

| Not built | Why |
|---|---|
| Average bank balance, turnover, inflow/outflow analysis | Financial analysis, not document validation. Belongs to underwriting. |
| Behavioural fraud patterns (circular transactions, salary inflation, holiday cash deposits, round-figure anomalies) | Named in the research as fraud markers, but these are **credit/fraud decisions** — explicitly out of scope (`PRODUCT_SPEC.md` §6). |
| Bank-account verification (penny drop / API) | External integration; does not test the hypothesis. |
| Cross-document PAN ↔ GST ↔ Bank | Post-MVP by design. |
| Signature or seal verification | No reliable deterministic basis; high false-positive cost. |
| Image submissions (photographs of statements) | Real and legitimate, but widens the OCR problem without proving anything new. Next document-scope increment. |

---

## Changelog

| Version | Change |
|---|---|
| 1.1 | Outcome resolution amended by implementation: `composite_gated` (ADR-018), downgrade reporting (ADR-019), and readability gating document-type classification. The reason-code catalogue moved into `validation_rules.yaml` so the tables in `PRODUCT_SPEC.md` §8 are executable rather than descriptive. |
| 1.0 | Initial rule set derived from Phase 1 research. `COMPLETENESS_DATE_GAP` reclassified from a blocking FIX to integrity signal R-INT-004, because a month with no transactions is legitimate for a dormant account and is named in the research as a fraud marker rather than an incompleteness indicator. |
