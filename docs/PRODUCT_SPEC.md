# Product Specification — MSME Document Pre-Validation Layer

**Status:** v1.1 (Phase 0 contract, amended by Phase 1 research)
**Source of truth for:** scope, outcome contract, reason codes, acceptance criteria.

> This document defines *what the system must do*. It does not define *how*.
> Architecture lives in `ARCHITECTURE.md`. Rule thresholds live in `VALIDATION_RULES.md`.

---

## 1. Problem statement

In digital MSME lending, document problems are discovered **after** submission, during
downstream processing. Each discovery triggers a rework loop:

```
Customer submits → downstream processing → issue found → Credit/Ops → Sales → Customer → re-upload
```

Every hop costs calendar time and human effort, and the borrower experiences an opaque
delay they cannot act on.

## 2. Product statement

A **Real-Time Document Collection & Pre-Validation Layer**. The borrower sends a document
to a WhatsApp number; the system validates it within seconds and returns one of three
outcomes with a **specific reason** and a **specific next action**.

```
Customer sends document → ingestion → extraction → validation → PASS | FIX | REVIEW
```

The core product is the **validation layer**. WhatsApp is the collection channel. This is
not an "AI OCR bot"; OCR and AI are implementation choices.

## 3. Hypothesis being tested

> A meaningful portion of document-related rework can be prevented by validating documents
> at the point of collection and immediately helping the customer resolve correctable issues.

**This is a hypothesis, not a measured claim.** Nothing in this project asserts a
FlexiLoans-specific document failure rate, re-upload rate, or internal workflow.

### Evidence tiering used throughout this project

| Tier | Meaning |
|---|---|
| **VERIFIED** | Publicly documented and citable. |
| **INDUSTRY** | Documented general industry practice, not specific to any one lender. |
| **ASSUMPTION** | Our product decision. Reasonable, stated, and changeable. |
| **HYPOTHESIS** | The thing the MVP exists to test. |
| **SIMULATED** | Modelled for demonstration; not a real measurement. |

Every rule in `VALIDATION_RULES.md` and every claim in the research documents carries one
of these tags.

## 4. Users

| User | Role in this product | What they get |
|---|---|---|
| **MSME borrower** (primary) | Sends the document over WhatsApp | Immediate, specific, actionable verdict |
| **Sales** (secondary) | Requests the document; consumes the verified result | Notification + verified document + structured data |
| **Operations** (secondary) | Resolves cases the system cannot decide | A REVIEW queue with reason and confidence breakdown |
| **Credit** (downstream) | Not touched by this product | A usable document, sooner |

## 5. Where the product sits

```
Customer shows interest → Sales contacts → Sales fills application
    → Sales: "Send your bank statement to this WhatsApp number"
    → ★ PRODUCT STARTS HERE
    → validation → PASS | FIX | REVIEW
    → verified document + structured data
    → Sales / downstream lending workflow (unchanged)
```

**Locked interaction:** the customer sends the document **directly** to the product's
WhatsApp number. Sales does not forward it.

In the MVP, WhatsApp is simulated in the frontend and application association is seeded
rather than integrated with a real lender system.

## 6. Document scope

**In scope (MVP):** 6-month current-account bank statement, PDF.

**Out of scope (MVP), architecture must remain extensible to:** GST certificate, GST
returns, PAN, ITR, business registration, other financial documents.

**Explicitly not built:** credit scoring, eligibility, approval, underwriting, disbursement,
fraud decisioning, LOS replacement, CRM, full operations platform, multi-document
cross-validation, production WhatsApp infrastructure, every Indian bank.

---

## 7. The outcome contract

The system returns exactly one of three outcomes. This contract is the product.

### PASS

> Every blocking rule passed, and the system is confident in that determination.

- No further customer action.
- Sales is notified; the verified document and structured data become available.
- Recorded as cleared. If this was the first submission against the requirement, it counts
  toward **First-Time Document Clearance Rate**.

### FIX

> A specific, customer-correctable problem was found, and the system is confident both
> about the problem and about how the customer can resolve it.

- The customer is told **what is wrong** and **what to do**, in that order.
- The customer re-uploads; the new file is a **new submission against the same
  requirement**, and is revalidated from scratch.
- A FIX is only valid if a concrete customer action exists. "Something is wrong" is never
  a FIX — it is a REVIEW.

### REVIEW

> The system cannot confidently decide, or the issue is not the customer's to fix.

Routed to Operations with: application reference, document, reason code, confidence
breakdown (which signals were weak), and the extracted summary. A human decides.

**Hard rule: uncertainty must never be resolved into PASS.**

### Choosing between FIX and REVIEW

| Situation | Outcome | Why |
|---|---|---|
| Rule failed, cause is clear, customer can act | **FIX** | Self-service resolution |
| Rule failed, cause unclear or not customer-actionable | **REVIEW** | Don't send the customer on a guess |
| Composite confidence below threshold | **REVIEW** | Never guess a verdict |
| Integrity/tamper signals raised | **REVIEW** | Never accuse a customer; never auto-reject |
| Identity match ambiguous | **REVIEW** | Name matching is fuzzy by nature |
| Identity clearly mismatched | **REVIEW** | May be legitimate (trade name vs legal name) |

---

## 8. Reason codes

Every non-PASS outcome carries exactly one **primary** reason code plus any number of
secondary ones. Codes are stable identifiers; customer-facing wording is separate and may
be rephrased without changing the code.

### File / ingestion

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `FILE_UNSUPPORTED_TYPE` | FIX | Yes — send a PDF |
| `FILE_TOO_LARGE` | FIX | Yes — send a smaller/split file |
| `FILE_CORRUPT` | FIX | Yes — re-download from bank and resend |
| `FILE_PASSWORD_REQUIRED` | *(not an outcome)* | Prompts for password |
| `FILE_PASSWORD_INCORRECT` | FIX | Yes — re-enter password |
| `FILE_EMPTY` | FIX | Yes — resend |
| `PROCESSING_ERROR` | REVIEW | No — the failure is ours, not the document's |

### Document type

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `DOC_TYPE_MISMATCH` | FIX | Yes — send the bank statement |
| `DOC_TYPE_UNCERTAIN` | REVIEW | No |
| `ACCOUNT_TYPE_NOT_CURRENT` | FIX | Yes — send the current-account statement |
| `ACCOUNT_TYPE_UNKNOWN` | REVIEW | No |

### Period

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `PERIOD_INSUFFICIENT_COVERAGE` | FIX | Yes — send the full required period |
| `PERIOD_STALE` | FIX | Yes — send an up-to-date statement |
| `PERIOD_NOT_FOUND` | REVIEW | No |

### Completeness

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `COMPLETENESS_MISSING_PAGES` | FIX | Yes — send all pages |
| `COMPLETENESS_BALANCE_BREAK` | REVIEW | No — could be missing pages or edited content |
| `COMPLETENESS_DATE_GAP` | *(reclassified — see below)* | No |

### Readability / extraction

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `READABILITY_NO_TEXT` | FIX | Yes — send a clearer copy / the bank's PDF |
| `READABILITY_POOR_SCAN` | FIX | Yes — send a clearer copy |
| `READABILITY_PARTIAL_CAPTURE` | FIX | Yes — send the bank's PDF, not a screenshot |
| `EXTRACTION_LOW_CONFIDENCE` | REVIEW | No |

`READABILITY_PARTIAL_CAPTURE` was added after a real submission (ADR-029). It is the
banking-app screenshot: legible, genuine, and missing a column. It is deliberately separate
from `POOR_SCAN` because the customer's next action is different — the problem is not the
quality of the image but its width.

### Integrity (never a fraud verdict)

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `INTEGRITY_SIGNALS_RAISED` | REVIEW | No |

### Identity

| Code | Default outcome | Customer-actionable |
|---|---|---|
| `IDENTITY_MISMATCH` | REVIEW | No |
| `IDENTITY_AMBIGUOUS` | REVIEW | No |

> **Amendment (v1.1, Phase 1).** `COMPLETENESS_DATE_GAP` was reclassified from a blocking
> FIX to the non-blocking integrity signal R-INT-004. Research showed that a calendar month
> with no transactions is legitimate for a dormant account, and is named in the literature as
> a *fraud marker* rather than an incompleteness indicator. Telling a customer to "send the
> continuous statement" when they already did would be a false FIX. See `VALIDATION_RULES.md` §6.

> Outcomes in this table are **defaults**. The confidence composer can escalate any FIX to
> REVIEW when confidence is low. It can never de-escalate a REVIEW to PASS.

---

## 9. Customer experience principles

The bot is a **structured document-verification assistant**, not an open-ended chatbot.

1. **Reason before action.** State what is wrong, then what to do.
2. **One primary issue at a time.** Listing six problems paralyses the customer. Secondary
   issues are shown only when they share the same corrective action.
3. **Concrete, checkable statements.** "01 May – 31 Aug 2026" beats "the period is wrong".
4. **No hedging, no apologising, no filler.**
5. **Never expose internals.** No reason codes, no confidence percentages, no stack traces,
   no model names in customer-facing text.
6. **Never show an unmasked account number** in any UI, message, log, or export.

### Reference messages

**PASS**
```
Bank statement verified.

Bank: HDFC Bank
Period: Apr–Sep 2026
Pages: 18

✓ Document type
✓ Required period
✓ Readability
✓ Completeness

No further action is required.
```

**FIX**
```
I found one issue.

Statement period:
❌ 01 May – 31 Aug 2026

Required:
✓ Latest 6 months

Please upload a statement covering the latest 6 months.
```

**Password**
```
This PDF is password protected.
Please enter the PDF password.
```

**REVIEW** (customer-facing wording never implies suspicion)
```
Thanks — I've received your bank statement.

This one needs a quick manual check by our team.
We'll get back to you shortly. No action needed from you right now.
```

---

## 10. Acceptance scenarios

These are the MVP's definition of done. Each becomes an automated end-to-end test in
`TEST_CASES.md`, driven by the synthetic corpus.

| # | Scenario | Input | Expected outcome | Primary reason code |
|---|---|---|---|---|
| 1 | Valid statement | 6-month current-account statement, native PDF | **PASS** | — |
| 2 | Wrong period | 4-month statement | **FIX** → re-upload → **PASS** | `PERIOD_INSUFFICIENT_COVERAGE` |
| 3 | Password protected | Encrypted valid statement | prompt → unlock → **PASS** | `FILE_PASSWORD_REQUIRED` |
| 4 | Wrong document | GST certificate | **FIX** | `DOC_TYPE_MISMATCH` |
| 5 | Corrupt / unreadable | Truncated PDF; image-only PDF with no legible text | **FIX** | `FILE_CORRUPT` / `READABILITY_NO_TEXT` |
| 6 | Incomplete statement | Pages removed mid-document | **FIX** | `COMPLETENESS_MISSING_PAGES` |
| 7 | Low-confidence extraction | Degraded scan, fields partially legible | **REVIEW** → Ops decision | `EXTRACTION_LOW_CONFIDENCE` |

### Scenario 2 must also demonstrate

- The re-upload is a **second submission against the same requirement**.
- The requirement is recorded as **cleared, but not first-time cleared**.
- The metric denominator counts **one requirement**, not two documents.

### Scenario 7 must also demonstrate

- Operations sees *which* signals were weak, not only a percentage.
- Both `[PASS]` and `[REQUEST NEW DOCUMENT]` decisions are possible and audited.

---

## 11. Non-functional requirements

| Area | Requirement |
|---|---|
| **Latency** | Verdict within ~10s for a native-text PDF; ~45s for an OCR path. Customer sees a processing state, never a blank wait. |
| **Determinism** | Given the same input and config, the outcome is identical. The LLM must not be able to change a verdict. |
| **Explainability** | Every outcome lists which rules ran, which passed, which failed, and on what evidence. |
| **Auditability** | Every state transition and human decision appends an immutable event. |
| **Configurability** | Thresholds, required period, and tolerances live in config, not code. |
| **Extensibility** | Adding a document type must not require changing the validation engine core. |

## 12. Security and data-handling requirements

Derived in detail in `RESEARCH_REGULATORY.md`. Binding requirements:

1. Transport over HTTPS.
2. Stored filenames are opaque and generated; never derived from user input.
3. File type allowlist enforced by **magic bytes**, not by extension or client MIME.
4. Enforced file-size cap.
5. Object storage is private. **No public document URLs, ever.** Downloads use short-lived
   signed URLs.
6. PDF passwords are **never logged, never persisted, and not hashed**. They exist in memory
   for the duration of one decryption attempt.
7. Decrypted documents are **not persisted**. Only the original encrypted file is stored.
8. Password attempts are rate-limited.
9. Account numbers are **masked** in every UI, API response, log, and export.
10. Least-privilege access between components; secrets never in frontend code.
11. Defined retention: documents and extractions are purgeable per requirement.
12. **Synthetic or anonymised documents only.** No real customer financial documents at any
    point in development, testing, or demonstration.

## 13. What the MVP deliberately does not prove

Stated plainly so the case study does not overclaim:

- It does not establish a real-world rework baseline; there is no production traffic.
- It does not prove extraction accuracy across all Indian banks — the corpus is synthetic
  and covers a representative sample.
- It does not detect document fraud. Integrity signals raise a human check; they are not a
  fraud verdict.
- It does not measure business impact. Volume is insufficient for statistical claims.

What it **does** prove: that a narrow, deterministic, explainable validation contract can
correctly triage realistic failure modes and close correctable ones through a self-service
loop, in seconds, at the point of collection.
