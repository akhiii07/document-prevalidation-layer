# MVP Plan & Product Requirements

## 1. Purpose

This is the **starting product/business requirements document for the
MVP build**.

Claude must use it to understand: - What problem we are solving - Why it
matters - Who the product is for - Where it fits in the lending
journey - What the MVP must and must not do - The customer experience -
Expected outcomes - Locked product decisions - What must be researched
before implementation

This is **not a system architecture or low-level technical design
document**. Technical architecture should be designed after
understanding these requirements.

## 2. Product Concept

Build a **borrower-facing WhatsApp document collection and
pre-validation layer for MSME lenders**.

The product is intended as a **generalizable lending capability**, not a
FlexiLoans-only feature. FlexiLoans is the reference context used to
understand the MSME lending journey and establish the problem.

### Core positioning

> Reduce document-related rework and application pendency by detecting
> and resolving document problems during document collection, before
> they require downstream Sales/Operations/Credit intervention.

WhatsApp is the **MVP interface/channel**. The core product is the
**real-time document collection + pre-validation capability**.

## 3. Business Problem

Digital MSME lending depends on documents such as bank statements, GST
documents and KYC/business proofs. FlexiLoans publicly requires
documents including a current-account bank statement and uses bank
statements/GST data in credit assessment. Public industry evidence also
shows document requirements, pending information and document-processing
workflows.

The specific internal FlexiLoans document-failure, exception or
re-upload rate is **not publicly established**.

Therefore the product is based on this hypothesis:

> **A meaningful portion of document-related rework can be prevented by
> validating documents at the point of collection and immediately
> helping the customer resolve correctable issues.**

This is a hypothesis to validate, not a claim about undisclosed
FlexiLoans metrics.

## 4. Core Pain Point

The fundamental problem is **document-related rework/pendency**.

``` text
Document requested
      ↓
Customer sends document
      ↓
Document enters processing
      ↓
Issue discovered
      ↓
Sales / Operations / Credit intervention
      ↓
Customer contacted
      ↓
Customer corrects / re-uploads
      ↓
Document processed again
      ↓
Application continues
```

### Customer

-   May not know whether the submitted document is usable.
-   May be asked to re-upload later.
-   May receive unclear correction requests.
-   Multiple back-and-forth interactions increase friction.

### Sales / Operations

-   Need to follow up when document issues are discovered.
-   Document exceptions create rework.
-   Applications can remain pending while documents are corrected.
-   The same document may be handled multiple times.

### Credit / Processing

-   Downstream processing can encounter document-quality, extraction,
    completeness or validation issues.
-   Processing cannot reliably continue until the issue is resolved.

## 5. Target Product Outcome

Move document problem detection upstream.

``` text
BEFORE
Submit → Process → Fail → Contact customer → Re-upload

AFTER
Submit → Immediate validation → Fix immediately → Verified document → Continue
```

### Primary business objective

Reduce:

> **Document re-upload requests flowing from Credit/Operations →
> Sales/Operations → Customer.**

### Intended impact

-   Reduced document-related rework
-   Reduced document clearance time
-   Reduced application TAT
-   Reduced manual intervention

The product should be evaluated on reduction of document friction, not
simply document-processing volume.

## 6. Primary Users

### Primary: MSME borrower

Directly interacts with the product through WhatsApp.

Experience must be simple, structured, immediate and actionable.

### Secondary: Sales / Operations

Sales initiates document collection. Operations handles uncertain cases.
The product should reduce document-related follow-up.

### Credit

Downstream stakeholder. The MVP does **not** replace credit underwriting
or make lending decisions.

## 7. Where the Product Fits

``` text
Customer shows interest in loan
        ↓
Sales contacts customer
        ↓
Sales fills application on behalf of customer
        ↓
Sales requests required documents
        ↓
Customer shares documents
        ↓
★ PRODUCT STARTS HERE
        ↓
WhatsApp Document Verification
        ↓
PASS / FIX / REVIEW
        ↓
Validated document / resolved exception
        ↓
Sales / Operations / downstream lending workflow
        ↓
Existing credit processing continues
```

### Locked interaction

Sales tells the customer to send the document directly to the product's
WhatsApp number.

``` text
Sales
  ↓
"Please send your bank statement to this WhatsApp number."
  ↓
Customer
  ↓
WhatsApp Verification Bot
```

For the MVP, application association can be simulated rather than
implementing a real lender identity/application integration.

## 8. MVP Document Scope

### Primary document

> **6-month current-account bank statement**

The architecture should remain extensible to other documents, but the
MVP must stay focused.

Future possibilities: - GST certificate - GST returns - PAN - ITR -
Business registration - Other financial documents

These are **not MVP scope**.

## 9. Why Bank Statement

Bank statements are a strong representative document because: - They are
publicly identified as a required lending document in the FlexiLoans
context. - They are used as inputs to credit assessment. - They have
multiple realistic failure modes. - They combine file-level,
extraction-level and business-level validation. - The same framework can
later support other financial documents.

## 10. MVP Failure Scenarios

Support approximately **5--7 realistic scenarios**.

### 1. Valid bank statement

`→ PASS`

### 2. Wrong statement period

Example: required latest 6 months, received 4 months.

`→ FIX → explain required period → customer re-uploads`

### 3. Password-protected PDF

`→ request password → temporary decryption → process → validate`

### 4. Wrong document

Expected bank statement, received GST certificate or another document.

`→ FIX → explain mismatch → request bank statement`

### 5. Corrupt / unreadable document

`→ FIX or REVIEW`, depending on whether the issue can be clearly
attributed to document quality.

### 6. Incomplete statement

Examples include missing pages or incomplete coverage.

`→ FIX → explain issue → request corrected document`

### 7. Low-confidence extraction

`→ REVIEW → Operations`

Do not attempt to support every possible failure mode in MVP.

## 11. Core Status Model

The MVP has exactly three primary outcomes.

### PASS

Document meets defined validation requirements.

``` text
PASS
 ↓
Notify Sales
 ↓
Sales downloads document
```

### FIX

A customer-correctable issue has been identified.

``` text
FIX
 ↓
Explain issue
 ↓
Customer action
 ↓
Re-upload
 ↓
Revalidate
```

### REVIEW

The system cannot confidently determine validity.

``` text
REVIEW
 ↓
Operations
 ↓
Application
Document
Reason
Confidence
 ↓
Human decision
```

The system must not force uncertain documents into PASS or FAIL.

## 12. Customer Experience

Build a **highly structured document-verification assistant**, not an
open-ended AI chatbot.

### Successful example

``` text
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

### Failure example

``` text
I found one issue.

Statement period:
❌ 01 May – 31 Aug 2026

Required:
✓ Latest 6 months

Please upload a statement covering the latest 6 months.
```

The bot must provide:

> **Specific failure reason + specific next action**

Avoid vague AI-generated responses.

## 13. Validation Philosophy

Do not rely on an LLM alone to decide whether a document is valid.

``` text
OCR / Document Parser
        ↓
Structured Data
        ↓
Deterministic Validation Rules
        +
AI-assisted Interpretation / Normalization
        ↓
PASS / FIX / REVIEW
```

### Deterministic rules

Use for: - File type - File size - Document period - Document type -
Basic completeness - Required field presence - Confidence thresholds -
Explicit business rules

### AI assistance

Use for: - Document classification - Extracting/normalizing fields
across bank formats - Interpreting semi-structured content - Handling
format variations - Producing structured output - Customer-friendly
explanation of identified issues

AI must **not** make credit or lending decisions.

## 14. Validation Requirements: Research Before Build

Do not invent bank-statement requirements.

Research representative Indian banks, initially: - SBI - HDFC Bank -
ICICI Bank - Axis Bank - Kotak Mahindra Bank

The exact set may be adjusted based on research.

For each bank, establish: - Available statement fields - Account-holder
information - Account-number representation - Statement-period
representation - Transaction dates - Transaction descriptions -
Debit/credit representation - Transaction amounts - Balance
representation - Page structure - Header/footer patterns - Relevant
bank-specific variations - PDF/scanned statement variations

Separate: 1. Common/universal fields 2. Bank-specific fields 3.
Lending-relevant fields

Actual validation rules must be based on this research.

## 15. RBI / Regulatory Research

Complete a focused review before implementing real document processing.

Research: - Financial-document handling - Customer consent - Data
minimization - Storage and retention - Access controls - Third-party
processing - AI/LLM handling of financial information - WhatsApp-based
document transmission - Auditability - Password/decrypted-document
handling

Do not assume RBI defines a universal bank-statement format. Regulatory
research should identify relevant constraints and influence
product/technical design.

## 16. Cross-Document Validation

Cross-document validation is an **important future capability**, but MVP
implementation should remain limited.

### MVP

Perform simple identity consistency checks where relevant.

Example:

``` text
Application business name
        ↕
Bank statement account-holder name
```

Possible outcomes: - Strong match → PASS - Clear mismatch → REVIEW /
FIX - Ambiguous match → REVIEW

### Post-MVP

``` text
             Application
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
      PAN        GST        Bank
       │          │          │
       └──────────┼──────────┘
                  ▼
       Cross-document engine
                  ↓
      Identity / consistency checks
```

Future checks may include: - PAN ↔ GST - PAN ↔ Bank - GST ↔ Bank -
Business entity ↔ account holder - Application data ↔ submitted
documents

The architecture should allow this later, but it is not the core MVP
implementation.

## 17. Password-Protected Documents

Support password-protected PDFs in MVP.

``` text
PDF received
     ↓
Password protected
     ↓
Bot:
"This PDF is password protected.
Please enter the PDF password."
     ↓
Customer enters password
     ↓
System decrypts temporarily
     ↓
Processing
     ↓
Validation
```

Do not persist passwords or decrypted documents unnecessarily.
Security/retention behavior must follow the research findings.

## 18. PDF Processing

Support both:

### Native text PDFs

``` text
PDF → Text extraction → Structured extraction
```

### Scanned/image PDFs

``` text
PDF → OCR → Structured extraction
```

Keep separate concepts: - Extraction failure - Low-confidence
extraction - Document invalidity

They are not equivalent.

## 19. Sales Experience

After PASS:

``` text
PASS
 ↓
Sales receives notification
 ↓
Sales downloads document
```

Sales should receive: - Application reference - Document status -
Verified document - Validation summary - Structured extracted data

No need to automate the complete Sales workflow.

## 20. Operations Experience

Operations dashboard is **minimal and low priority**.

Purpose: demonstrate REVIEW handling.

``` text
Application: FLX-10231

Document: Bank Statement

Status: REVIEW

Reason:
Low extraction confidence

Confidence:
61%

[View Document]

[PASS] [REQUEST NEW DOCUMENT]
```

No sophisticated operations platform is required.

## 21. LOS / Downstream Scope

The MVP will **not integrate with a real FlexiLoans LOS**.

Use a minimal/mock downstream interface showing what the product would
hand to the lender.

``` text
Application: FLX-10231

Bank Statement
Status: VERIFIED

Bank: HDFC Bank
Period: Apr–Sep 2026
Pages: 18
Account Holder: ABC Traders

Validation:
Document Type     PASS
Period            PASS
Completeness      PASS
Readability       PASS

Confidence: 97%
```

Conceptual handoff:

``` text
Verified Document
      +
Structured Data
      +
Validation Result
      ↓
Existing LOS
```

## 22. MVP Interface Decision

Use:

> **Functional backend + simulated WhatsApp experience + minimal
> Operations dashboard**

rather than spending early effort on real WhatsApp infrastructure.

``` text
Simulated WhatsApp UI
        ↓
Real backend
        ↓
Real PDF processing
        ↓
Real extraction
        ↓
Real validation
        ↓
Real correction loop
        ↓
Mock Operations / LOS output
```

Real WhatsApp integration can replace the simulated interface later.

## 23. Real vs Mock

  Component                              MVP
  -------------------------------------- -------------------------------
  Customer document                      Real synthetic/anonymized PDF
  PDF processing                         **Real**
  OCR                                    **Real**
  Data extraction                        **Real**
  Validation rules                       **Real**
  AI-assisted extraction/normalization   **Real where useful**
  Correction loop                        **Real**
  Structured output                      **Real**
  WhatsApp UI                            Simulated
  WhatsApp infrastructure                Mocked
  FlexiLoans LOS                         Mocked
  Operations dashboard                   Minimal
  Credit underwriting                    **Not built**
  Loan approval                          **Not built**
  Disbursement                           **Not built**

## 24. Synthetic Test Data

Use only synthetic/anonymized documents.

Test corpus should represent realistic bank-statement structures and
controlled failure cases.

``` text
Bank Statement Test Corpus
│
├── Valid
│   ├── SBI-style
│   ├── HDFC-style
│   ├── ICICI-style
│   └── Axis-style
│
├── Wrong Period
├── Password Protected
├── Wrong Document
├── Incomplete
├── Poor Quality
└── Low Confidence
```

Actual bank formats and fields must be determined by research before
finalizing the corpus.

## 25. Complete MVP Journey

``` text
Sales asks customer for bank statement
                ↓
Customer receives WhatsApp instructions
                ↓
Customer uploads bank statement
                ↓
Document received
                ↓
Security / file / metadata checks
                ↓
       ┌────────┴────────┐
       ▼                 ▼
 Processable        Unprocessable
       │                 │
       ▼                 ▼
Extraction          Explain issue
       │                 │
       ▼                 ▼
Structured Data     Customer Fix
       │                 │
       ▼                 ▼
Validation          Re-upload
       │                 │
   ┌───┼────┐            │
   ▼   ▼    ▼            │
 PASS FIX REVIEW          │
   │   │    │             │
   │   │    ▼             │
   │   │  Operations      │
   │   │                  │
   │   └──────────────────┘
   │
   ▼
Verified Document
+
Structured Output
   ↓
Sales Notification
   ↓
Sales Downloads Document
   ↓
Downstream Lending Workflow
```

## 26. Primary Product Metric

### First-Time Document Clearance Rate

``` text
Documents verified without requiring re-upload
──────────────────────────────────────────────
Total documents submitted
```

This directly measures the core problem.

### Supporting metrics

-   Document re-upload rate
-   Document pendency rate
-   Document clearance time
-   Manual review rate
-   Average correction attempts
-   Failure reason distribution
-   Application TAT

The prototype should instrument these events even though it will not
have enough real-world volume to establish statistically meaningful
business impact.

## 27. MVP Success Criteria

The MVP must reliably demonstrate:

``` text
Valid statement
→ PASS
```

``` text
Wrong period
→ FIX
→ Customer re-uploads
→ PASS
```

``` text
Password-protected PDF
→ Request password
→ Decrypt/process
→ PASS
```

``` text
Wrong document
→ FIX
→ Customer re-uploads
```

``` text
Incomplete document
→ FIX
```

``` text
Unreadable / extraction failure
→ FIX or REVIEW
```

``` text
Low-confidence extraction
→ REVIEW
→ Operations
```

For a successful document:

``` text
Real PDF
 ↓
Real extraction
 ↓
Real validation
 ↓
Real correction where required
 ↓
Real structured output
 ↓
Sales notification
 ↓
Sales downloads document
```

## 28. High-Level Build Phases

### Phase 1 --- Product Contract

Define: - Exact document scope - Validation rules - PASS / FIX / REVIEW
definitions - Failure scenarios - Customer actions - Sales output -
Operations output - MVP acceptance criteria

Do not code against undefined requirements.

### Phase 2 --- Research

Research: - Indian bank statement formats - Representative bank fields -
Bank-specific variations - MSME lending document validation practices -
Real document failure modes - RBI/data-handling requirements - Relevant
document-processing practices

Research findings must directly inform validation rules.

### Phase 3 --- Synthetic Document Corpus

Create realistic synthetic/anonymized documents covering: - Valid -
Wrong period - Password protected - Wrong document - Incomplete - Poor
quality - Low-confidence extraction

### Phase 4 --- Customer Experience

Build:

``` text
Request → Upload → Processing → Result → Correction → Re-upload → Verification
```

### Phase 5 --- Document Processing

``` text
Document
 ↓
Ingestion
 ↓
File checks
 ↓
Security checks
 ↓
OCR / parsing
 ↓
Structured extraction
```

### Phase 6 --- Validation

``` text
Structured data
 ↓
Validation rules
 ↓
Confidence handling
 ↓
PASS / FIX / REVIEW
```

This is the **core product-engineering phase**.

### Phase 7 --- Correction Loop

Implement real customer resolution for supported failure scenarios.

The system must tell the customer: 1. What went wrong 2. Why it matters
3. What to do 4. When to re-upload

### Phase 8 --- Sales / Operations Output

Build: - Sales notification - Verified-document download - Validation
summary - Minimal Operations review interface

### Phase 9 --- Analytics

Instrument: - document received - processing started - validation
completed - PASS - FIX - REVIEW - correction requested - re-upload -
verified - human review - sales notification

## 29. Build Priorities

Priority order:

``` text
1. Validation correctness
2. Correction experience
3. Real PDF extraction
4. PASS / FIX / REVIEW workflow
5. Structured output
6. Customer UX
7. Minimal Operations dashboard
8. Analytics
9. WhatsApp integration
```

Do not spend most development effort on WhatsApp infrastructure. The
product hypothesis is about **reducing document rework**, not proving
messaging infrastructure.

## 30. Explicit Out of Scope

Do not expand MVP into: - Credit scoring - Loan eligibility - Loan
approval - Underwriting - Disbursement - Fraud decisioning - Full LOS
replacement - Full CRM - Full Operations platform - Multi-document
lending automation - Complete cross-document intelligence -
Production-scale WhatsApp infrastructure - Every Indian bank - Every
possible document failure

These may become future capabilities but are not required to prove the
MVP hypothesis.

## 31. Generalizable Product Direction

Although the MVP uses a bank statement, the underlying capability should
eventually work as:

``` text
                    Document
                       │
                       ▼
              Collection Layer
                       │
                       ▼
             Document Processing
                       │
                       ▼
              Validation Engine
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
             PASS     FIX     REVIEW
                       │
                       ▼
             Customer Resolution
                       │
                       ▼
              Structured Output
                       │
                       ▼
                Lender Workflow
```

The MVP should prove a **general document-friction solution using one
high-value document type**, rather than building a FlexiLoans-specific
one-off.

## 32. Final Product Thesis

> **Digital lending becomes less digital when document problems are
> discovered only after submission.**

The product moves validation to the point of collection:

``` text
BEFORE

Customer
 ↓
Submit
 ↓
Downstream processing
 ↓
Problem discovered
 ↓
Sales / Ops
 ↓
Customer
 ↓
Re-upload


AFTER

Customer
 ↓
Submit
 ↓
Real-time validation
 ↓
Fix immediately if required
 ↓
Verified document
 ↓
Sales / downstream workflow
```

The MVP exists to prove whether this change can **reduce document
re-uploads and document-related processing time while improving the
borrower experience**.
