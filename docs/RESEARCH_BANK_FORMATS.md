# Research — Indian Bank Statement Formats

**Status:** v1.0 (Phase 1)
**Purpose:** derive validation rules from evidence, not from convenience.

Every claim carries an evidence tag: **VERIFIED** (publicly documented and citable),
**INDUSTRY** (documented general practice), **INFERRED** (reasonable deduction from
sources), **ASSUMPTION** (our decision), **UNVERIFIED** (needs confirmation against real
samples before it becomes a blocking rule).

> **Honesty note.** Public sources describe Indian bank statement structure at a *general*
> level and describe password conventions per bank. They do **not** publish
> machine-readable, per-bank column specifications. Where this document is specific about a
> single bank's layout, it is tagged **UNVERIFIED** and is used only to shape the synthetic
> corpus — never as a blocking validation rule. Blocking rules are built only on the
> **common field model** in §3.

---

## 1. Why the bank statement is the right MVP document

**VERIFIED.** FlexiLoans' public business-loan documentation lists **"Last 6 months of bank
statement of current account"** as a required document, alongside KYC/PAN, address proof,
and business KYC (GST registration certificate or Shop & Establishment certificate). For
loans above ₹20 lakh it additionally requires 2 years' audited financials, 2 years' ITR and
6 months' GST returns. FlexiLoans also states the process accepts digital copies and is
paperless.

**VERIFIED.** The broader Indian market norm matches: lenders typically require **6–12
months** of current-account statements; IIFL publicly states "last six months for the
operative account, 12 months preferable for the maximum amount."

**Consequence for scope.** The MVP's target — a **6-month current-account bank statement** —
is a real, publicly documented requirement, not an invented one. The 6-month figure in
`VALIDATION_RULES.md` is VERIFIED.

---

## 2. Statement delivery and encryption

**VERIFIED.** Indian bank e-statements are commonly delivered as **password-protected PDFs**,
with the password derived from information only the account holder should know.

**INDUSTRY.** Encryption is real PDF encryption (AES-128/256), not a viewer-level flag —
so a password is genuinely required to read the content stream.

### Password conventions (VERIFIED, but conflicting across sources)

| Bank | Convention reported | Confidence |
|---|---|---|
| **HDFC Bank** | Customer ID (8–10 digits), **not** date of birth | VERIFIED — consistent across all sources checked |
| **SBI** | Varies by source of the PDF: DOB `DDMMYYYY` (YONO app) vs **11-digit account number** (Net Banking). One source also reports "last 5 digits of mobile + DOB `DDMMYY`" for app/email statements | VERIFIED that it varies; exact mapping **UNVERIFIED** |
| **ICICI Bank** | Source A: DOB `DDMMYYYY`. Source B: first 4 letters of name (lowercase) + DOB `DDMM` | **CONFLICTING** |
| **Axis Bank** | Source A: DOB `DDMMYYYY`. Source B: first 4 letters of name (CAPITALS) + DOB `DDMM` | **CONFLICTING** |
| **Kotak Mahindra** | DOB `DDMMYYYY` | VERIFIED (single source) |
| Others (Yes, Canara, PNB, BoB, Union, BOI, Federal, IndusInd, IDFC First, RBL, StanChart) | DOB `DDMMYYYY` | VERIFIED (single source) |
| South Indian Bank | First 4 letters of name (lowercase) + last 4 digits of account number | VERIFIED (single source) |
| HSBC / legacy Citi | First 4 letters of surname + DOB `DDMM` | VERIFIED (single source) |

### Product consequence — important

Because these conventions **conflict between sources and vary by delivery channel within a
single bank**, the product must **never attempt to derive, guess, or brute-force a
password**. It prompts the customer, who knows it.

A password *hint* may be shown as a convenience **only** where the convention is
unambiguous (HDFC = Customer ID), and must be phrased as a hint, never an instruction. This
is a UX nicety, not a validation mechanism.

---

## 3. Common field model (the basis for all blocking rules)

**INDUSTRY / INFERRED.** Across Indian bank statements the following fields are universally
present in some form. This is the canonical schema the extraction layer normalises into.

### 3.1 Header block — account and statement identity

| Canonical field | Present across banks | Notes |
|---|---|---|
| `bank_name` | Always | Usually in a logo/letterhead; also inferable from IFSC prefix |
| `account_holder_name` | Always | For MSMEs this is the **entity/trade name**, which may differ from the legal name on the application |
| `account_number` | Always | Formatting and length vary by bank (SBI commonly 11 digits) |
| `account_type` | Usually | May be stated as "Current Account", "CA", "Current"; **not guaranteed to be explicit** |
| `ifsc` / `branch` | Usually | Useful as a secondary bank-identification signal |
| `statement_period_start` / `_end` | Always | Representation varies — see §3.3 |
| `address` | Usually | Not lending-relevant for this MVP; not extracted (data minimisation) |

### 3.2 Transaction table

**INDUSTRY.** The near-universal column set is **date, narration/description, debit, credit,
balance**.

| Canonical field | Notes |
|---|---|
| `txn_date` | Sometimes accompanied by a separate `value_date` |
| `description` | **Frequently multi-line** — a single transaction wraps across rows. This is the single biggest extraction hazard. Contains UTR numbers, reference IDs, branch codes, internal notes |
| `debit` / `credit` | Two representations exist: (a) separate Debit and Credit columns, (b) a single Amount column with a `Dr`/`Cr` marker. Both must be supported |
| `amount` | Derived from the above into a signed canonical value |
| `balance` | Running balance. **May not always be present** — some formats omit it |
| `reference` | Cheque/reference number, where a separate column exists |

### 3.3 Known variation axes (INDUSTRY)

These are the things the normalisation layer must absorb:

1. **Date formats** — `DD/MM/YYYY`, `DD-MM-YYYY`, `DD Mon YYYY`, and month-name forms.
   *Ambiguity risk:* `03/04/2026` is 3 April in India, not 4 March. Parsing must be
   day-first by default and must flag genuine ambiguity rather than guess.
2. **Number formatting** — separator conventions differ; Indian lakh/crore grouping
   (`1,23,456.78`) appears alongside Western grouping.
3. **Column labels** — "Narration" / "Description" / "Particulars" / "Transaction Remarks".
4. **Debit/credit representation** — separate columns vs. single amount + `Dr`/`Cr`.
5. **Multi-line narration** — rows do not map 1:1 to transactions.
6. **Repeated headers** — column headers and account headers repeat on every page.
7. **Page structure** — "Page X of Y" footers are common but not universal.
8. **Native vs scanned** — bank-issued PDFs have a native text layer; customer-supplied
   copies are often photographs or scans of printouts, requiring OCR.

### 3.4 Lending-relevant subset

Of everything available, the MVP extracts and stores only what validation needs
(data minimisation — see `RESEARCH_REGULATORY.md`):

| Field | Why it is needed |
|---|---|
| `bank_name` | Customer-facing confirmation; profile selection |
| `account_holder_name` | Identity consistency check vs. application |
| `account_number` (**masked at rest and in transit**) | Continuity across pages; duplicate-submission detection |
| `account_type` | The requirement is specifically a *current* account |
| `statement_period_start` / `_end` | The period rule — the single most common failure mode |
| `page_count` | Completeness |
| `transactions[]` (date, description, debit, credit, balance) | Balance-continuity completeness and date-gap detection |

**Not extracted:** customer address, phone, email, and any transaction-level counterparty
analysis. The MVP validates a document; it does not analyse finances.

---

## 4. Bank-specific notes (UNVERIFIED — corpus shaping only)

These inform the synthetic corpus so that layouts differ meaningfully from one another.
**No blocking rule depends on any of them.**

| Bank | Corpus treatment |
|---|---|
| **SBI** | Separate Debit/Credit columns; `DD Mon YYYY` dates; 11-digit account number; dense, utilitarian layout; separate value-date column |
| **HDFC Bank** | "Narration" column label; separate Withdrawal/Deposit amount columns; `DD/MM/YY` dates; "Page X of Y" footer; closing-balance summary block |
| **ICICI Bank** | "Transaction Remarks" label; single amount column with `Dr`/`Cr` marker in one variant; `DD-MM-YYYY` dates |
| **Axis Bank** | "Particulars" label; separate Debit/Credit columns; running balance always present; per-page opening/closing balance |
| **Kotak Mahindra** | Compact layout; `DD-MM-YYYY`; narration frequently multi-line with long UPI/UTR strings |

**Action required before any bank-specific rule is promoted to blocking:** validate against
real anonymised samples. Until then, bank profiles affect *parsing hints only*, and a
profile miss degrades to the generic parser plus a confidence penalty — never to a failed
verdict.

---

## 5. Document-integrity findings

**INDUSTRY.** Tampered bank statements are a material, quantified problem in Indian digital
lending. Published industry figures include ~12.3% of statements submitted to NBFCs
containing tampering or misrepresentation, and ~5% of statements submitted through online
loan channels being tampered or fabricated. *(These are vendor-published figures from
bank-statement-analysis providers and should be treated as directional, not authoritative.)*

**INDUSTRY.** The detection signals consistently named across sources:

| Signal | Detectable deterministically? | Our use |
|---|---|---|
| **Running-balance mismatch** (computed balance ≠ stated balance) | **Yes** — pure arithmetic | Primary completeness + integrity check |
| **Missing pages** | Yes — page continuity + balance carry-over | Completeness |
| PDF metadata: creator/producer/date stamps | Yes | Integrity signal |
| Font / alignment / layout anomalies | Partially | Integrity signal (weak) |
| Inconsistent print quality across pages | Partially (OCR-path only) | Integrity signal (weak) |
| Pixel artifacts around edited text | Not at MVP cost | Out of scope |
| Behavioural fraud patterns (circular transactions, holiday cash deposits, round-figure anomalies) | Yes, but this is **financial analysis, not document validation** | **Out of scope** — belongs to underwriting |

### Two conclusions that shaped the design

1. **Balance-continuity checking is validated by the evidence**, not merely convenient. It
   is the one check a genuine statement cannot fail by accident, and it simultaneously
   catches missing pages and post-issuance edits. → ADR-007.
2. **Integrity signals must not produce a verdict.** The same sources that describe these
   signals also describe their ambiguity. A missing page and an edited figure produce the
   *same* balance break. The system therefore reports `COMPLETENESS_BALANCE_BREAK` /
   `INTEGRITY_SIGNALS_RAISED` and routes to **REVIEW** — never a rejection, never an
   accusation. → ADR-008.

---

## 6. What this research does *not* establish

Stated explicitly so downstream documents do not overclaim:

- **No universal format.** RBI does not define a standard bank-statement layout. Every
  structural rule must therefore be tolerant and evidence-producing rather than rigid.
- **No public per-bank column specification.** §4 is corpus-shaping only.
- **No published recency norm.** Sources establish the *length* required (6–12 months) but
  **not** how recent the statement's end date must be. The 35-day recency tolerance in
  `VALIDATION_RULES.md` is therefore an explicit **ASSUMPTION**, config-driven and flagged.
- **No FlexiLoans-specific failure, re-upload, or rework rate.** None is published. The
  product hypothesis remains a hypothesis.

---

## Sources

- [FlexiLoans — Documents Required for a Business Loan](https://flexiloans.com/business-loan/document-required)
- [FlexiLoans — Business Loan Eligibility Criteria](https://flexiloans.com/business-loan-eligibility-criteria)
- [IIFL Finance — How bank statement analysis affects business loan approval](https://www.iifl.com/blogs/business-loan/how-bank-statement-analysis-affects-business-loan-approval)
- [Indian Bank Statement PDF Passwords — all banks table](https://mybankstatementanalysis.com/blog/indian-bank-statement-pdf-passwords)
- [HDFC Bank statement password format](https://mybankstatementanalysis.com/blog/hdfc-bank-statement-password)
- [Bank Statement Password — PDF formats for every Indian bank](https://www.scanpilot.ai/tools/bank-statement-password/)
- [PDF Bank Statements Converter — Indian banks](https://pdfbankstatementsconverter.com/indian-banks/)
- [Precisa — Bank statement fraud detection India](https://precisa.in/blog/bank-statement-fraud-detection-india-2026/)
- [HyperVerge — Bank statement analysis](https://hyperverge.co/blog/bank-statement-analysis/)
- [ProAnalyser — Bank statement PDF validation guide](https://proanalyser.in/bank-statement-pdfs-smart-analysis-to-prevent-loan-fraud/)
