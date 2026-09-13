# Overview

## Business Need

FlexiLoans' MSME lending journey depends on financial and business
documents such as **bank statements, GST documents, and KYC/business
proofs**. Public FlexiLoans information confirms that bank statements
and GST data are used in credit assessment and that customers can submit
documents through digital channels.

The product opportunity is to reduce **document-related rework and
pendency** by identifying and resolving document issues at the point of
collection, before the document enters downstream processing.

> **Core hypothesis:** A meaningful portion of document-related rework
> can be prevented through real-time document pre-validation.

This is a product hypothesis, not a claim that FlexiLoans publicly
reports a specific document-failure or abandonment rate.

------------------------------------------------------------------------

## Pain Points

### Customer

-   May submit an incomplete, incorrect, unsupported, unreadable, or
    otherwise unusable document without knowing immediately.
-   May need to re-upload documents or provide clarification.
-   May not know exactly what needs to be corrected.

### Operations / Sales

-   Document issues may require customer follow-ups.
-   Rework can create application pendency.
-   Corrected documents may need to be processed again.

### Credit / Processing

-   Downstream processing can be blocked by document-quality,
    extraction, completeness, or validation issues.
-   Unresolved document exceptions delay progression of the application.

------------------------------------------------------------------------

## Current State

``` text
Customer
   ↓
Document Collection
   ↓
File / Document Processing
   ↓
Extraction & Validation
   ↓
Issue Discovered
   ↓
Ops / Sales Intervention
   ↓
Customer Contacted
   ↓
Correction / Re-upload
   ↓
Reprocessing
   ↓
Application Continues
```

FlexiLoans publicly supports document upload and bank-statement
fetching, and its published requirements include bank statements and
GST/business documents. Its public material also states that bank
statements and GST returns are used in credit assessment.

The exact internal FlexiLoans exception/rework architecture is not
publicly documented; therefore, the above represents the **problem
hypothesis / target workflow to validate**, not a claim about
undisclosed internal systems.

------------------------------------------------------------------------

## Suggested State

Build a **Real-Time Document Collection & Pre-Validation Layer**, using
WhatsApp as the MVP interface.

WhatsApp is the collection interface; the core product is the **document
validation layer**.

``` text
Customer
   ↓
WhatsApp
   ↓
Document Ingestion
   ↓
Security + File + Metadata Checks
   ↓
OCR / Parser
   ↓
Data Extraction
   ↓
Document Validation
   ↓
   ├── PASS ───────→ Structured Data → Existing LOS
   │
   ├── FIX NEEDED ─→ Customer Correction → Re-upload
   │
   └── REVIEW ─────→ Human / Ops Review
```

### MVP Scope

1.  **Document ingestion**
    -   Receive the document securely.
    -   Associate it with the correct application/document requirement.
2.  **Pre-processing checks**
    -   Security scanning.
    -   File type, size, corruption and basic format checks.
    -   Basic metadata/document requirement checks.
3.  **Extraction**
    -   OCR / document parser.
    -   Extract relevant document data into structured fields.
4.  **Document validation**
    -   Check completeness, readability, required period, document type
        and relevant business rules.
    -   Identify issues that can be corrected by the customer.
5.  **Real-time resolution**
    -   Explain the specific issue to the customer.
    -   Request the required correction or re-upload.
    -   Route unresolved/low-confidence cases for human review.
6.  **LOS handoff**
    -   Pass verified structured document data into the existing lending
        workflow.
    -   Do not replace credit underwriting or approval in the MVP.

------------------------------------------------------------------------

## High-Level Product Plan

### Phase 1 --- Understand

-   Map the document collection → processing → validation workflow.
-   Identify the major document failure/rework categories.
-   Establish baseline metrics.

### Phase 2 --- Build MVP

-   WhatsApp document collection.
-   Ingestion and security checks.
-   File and metadata validation.
-   OCR/document parsing.
-   Document-level validation.
-   Real-time correction prompts.

### Phase 3 --- Integrate

-   Send verified structured data to the existing LOS.
-   Route unresolved cases to human/operations review.

### Phase 4 --- Measure

**Primary metric**

> **First-Time Document Clearance Rate**

**Supporting metrics** - Document re-upload rate - Document pendency
rate - Manual review rate - Document clearance time - Application TAT

**Business hypothesis**

> Real-time document pre-validation should reduce document-related
> rework and improve the speed at which applications progress through
> the lending funnel.

------------------------------------------------------------------------

## Evidence Basis

The case is based on publicly available information:

-   **FlexiLoans:** Public application/FAQ materials show requirements
    including bank statements and GST/business documents; FlexiLoans
    states that bank statements and GST returns are used in credit
    assessment.
-   **Lendingkart:** Public API/partner documentation explicitly
    discusses document collection, application delays caused by document
    requirements, and pending information/document requests.
-   **Industry document-processing platforms:** Providers such as
    Perfios publicly offer financial-document extraction, analysis and
    validation capabilities integrated with lending systems.

These sources establish that document collection, processing and
exception handling are real components of digital lending workflows.
They do **not** establish a publicly reported FlexiLoans-specific
document failure rate.

## Important Product Principle

The case study should be positioned as:

> **Reducing document-related rework in MSME lending through real-time
> pre-validation.**

It should **not** be positioned as:

> **Building an AI/OCR WhatsApp bot.**

The AI/OCR and WhatsApp components are implementation choices supporting
the underlying product problem.
