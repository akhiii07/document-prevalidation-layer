# Research — Regulatory & Data-Handling Constraints

**Status:** v1.0 (Phase 1)
**Purpose:** identify the constraints that shape architecture and data handling.

> **Scope note.** This is a product-engineering reading of publicly reported regulatory
> requirements, done to inform design decisions. It is **not legal advice** and it is not a
> compliance certification. A real deployment would require review by qualified counsel and
> the lender's compliance function.

Evidence tags: **VERIFIED** (publicly reported), **INFERRED**, **ASSUMPTION**.

---

## 1. The central finding: data localisation

**VERIFIED.** Under the **RBI (Digital Lending) Directions, 2025**:

- All borrower data must be **stored within India**.
- Where data is **processed abroad**, it must be **deleted from foreign servers and brought
  back to India within 24 hours**.
- Data collection by Digital Lending Apps and Lending Service Providers must be **strictly
  need-based**, purpose-specific, and supported by **prior, explicit consent with an audit
  trail**.
- LSPs may retain borrower data **only as long as necessary**.
- Regulated Entities must have **written contracts** with LSPs defining roles and
  responsibilities, and must **periodically review** LSP conduct.
- DLAs are **prohibited** from accessing phone resources (files, media, contacts, call logs,
  telephony) without need-based justification and explicit consent.

### Direct design consequences

| Requirement | How this product responds |
|---|---|
| Store borrower data in India | Object storage and database must be India-region. Local-first in the MVP; `asia-south1` if deployed. |
| Foreign processing → delete within 24h | **This is the decisive argument against Google Document AI's specialised processors**, which are not offered in `asia-south1`. Sending statements to a US/EU processor would create a 24-hour deletion obligation we cannot verify or enforce. → **ADR-002 is regulatory, not merely economic.** |
| Need-based, minimal collection | We extract only the fields in `RESEARCH_BANK_FORMATS.md` §3.4. No address, no phone, no counterparty analysis. |
| Consent with audit trail | Every document event is recorded immutably in `document_events`. |
| Retain only as long as necessary | Documents and extractions must be purgeable; retention is a configured policy, not "forever". |
| No device-resource access | Not applicable — this is a messaging-channel product, not a mobile app with permissions. Worth stating because it is a differentiator: **the WhatsApp channel avoids the app-permission surface the Directions restrict.** |

---

## 2. DPDP Act 2023 and DPDP Rules

**VERIFIED.** The Digital Personal Data Protection Act, 2023 and its operationalising Rules
impose on data fiduciaries:

- **Purpose limitation** — personal data retained only as long as necessary for the
  specified purpose.
- **Storage limitation / erasure** — erase personal data once the purpose is met and
  retention is not legally required.
- **Accuracy and completeness** — reasonable efforts to keep data accurate.
- **Security safeguards** to prevent breach, and **breach notification** to the Data
  Protection Board and affected persons.
- **Logs retention** — certain logs retained for at least one year for lawful-request and
  investigation purposes, then erased unless another law requires longer.
- **Advance notice before erasure** — data principals informed at least 48 hours prior.

### Direct design consequences

| Requirement | How this product responds |
|---|---|
| Purpose limitation | A submitted statement is used to validate *that document requirement*. It is not repurposed. |
| Storage limitation | Configurable retention on documents and extractions; a purge path exists from day one rather than being retrofitted. |
| Audit logs ≥ 1 year | `document_events` is append-only and retained separately from document content — so content can be purged while the audit trail survives. **This separation is a design requirement, not an implementation detail.** |
| Security safeguards | Private storage, signed URLs, magic-byte allowlisting, size caps, no public URLs. |
| Accuracy | Every extracted field carries provenance and confidence; nothing is silently inferred. |

---

## 3. Password-protected documents

**VERIFIED.** E-statements are commonly encrypted with real PDF encryption, with passwords
derived from account-holder knowledge (see `RESEARCH_BANK_FORMATS.md` §2).

**INFERRED.** A PDF password is, functionally, a credential derived from personal data
(customer ID, date of birth, account number, name). Under purpose limitation and data
minimisation it must be treated as a secret with the shortest possible life.

### Binding rules adopted

1. The password is accepted over the same encrypted transport as the document.
2. It is held **in memory only**, for the duration of a single decryption attempt.
3. It is **never logged** — not at any level, not in request bodies, not in error traces.
4. It is **never persisted** — not in the database, not in object storage, **and not as a
   hash**. A hash of a DOB- or customer-ID-derived password is trivially reversible and is
   itself personal data; storing one would create a liability with no benefit.
5. The **decrypted document is not persisted**. Only the original encrypted file is stored.
   Decryption is repeated on demand if reprocessing is required — which means the customer
   is asked again, which is correct.
6. Password attempts are **rate-limited** per document.
7. Failure messages never reveal whether a password was "close", and never echo the input.

**ASSUMPTION:** re-prompting the customer on reprocessing is acceptable friction. The
alternative — caching the password or the plaintext — is not.

---

## 4. WhatsApp as a transmission channel

**INFERRED.** WhatsApp provides end-to-end encryption in transit, but a business-API
deployment introduces a processor (Meta) into the data path, and message media is retained
on Meta infrastructure for a period outside the lender's control. Under the localisation
requirement in §1, this needs specific contractual and architectural treatment.

**Consequence for the MVP.** WhatsApp is **simulated**. This is not only a scope decision —
it means the MVP does not, at any point, place financial documents into a third-party
messaging processor. A production integration would require a documented assessment of
Meta's media handling, retention, and region against the Digital Lending Directions.

**This is a genuine open risk in the product thesis, and the case study should say so**
rather than presenting WhatsApp as obviously compliant.

---

## 5. AI/LLM handling of financial data

**INFERRED** (from §1 localisation + §2 purpose limitation; no source examined imposes a
rule specific to LLMs on financial documents).

Constraints adopted:

1. An LLM call is **foreign processing** unless the model is served in-region. Any LLM in
   the pipeline therefore inherits the localisation and 24-hour-deletion obligations.
2. Consequently the LLM is given **the minimum necessary**: normalised field fragments and
   layout context, **never** the full document, never the account number, never the
   transaction ledger in bulk.
3. The LLM **cannot influence a verdict** (ADR-001). This is a compliance property as much
   as an engineering one: an auditable lending workflow needs a deterministic, reproducible
   decision path.
4. The deterministic fallback means the system **functions with the LLM disabled entirely** —
   which is the correct posture if a compliance review later rules LLM processing out.

**ASSUMPTION:** for the MVP the LLM is used on synthetic documents only, so no real personal
data is transmitted. This must be re-evaluated before any real-data deployment.

---

## 6. Requirements adopted into the product

These are now binding and are reflected in `PRODUCT_SPEC.md` §12.

### Data handling
- India-region storage and processing; no foreign processing of borrower documents.
- Extract only the lending-relevant field subset; discard the rest.
- Configurable retention with a working purge path.
- Audit events stored separately from document content, so content purge does not destroy
  the audit trail.

### Secrets and credentials
- PDF passwords: memory-only, never logged, never persisted, never hashed, rate-limited.
- Decrypted content never written to disk.
- Application secrets in environment/secret manager, never in frontend code or the repo.

### Access control
- Private object storage; no public document URLs.
- Short-lived signed URLs for download.
- Operations endpoints gated (shared secret at MVP; real identity in production — ADR-011).
- Least privilege between components.

### Data exposure
- Account numbers masked in every UI, API response, log, and export.
- No customer-facing exposure of reason codes, confidence scores, or internals.

### Auditability
- Append-only event log for every state transition and every human decision.
- Human review decisions attributable and timestamped.

### Development practice
- **Synthetic or anonymised documents only**, at every stage. No real customer financial
  documents in development, testing, or demonstration.

---

## 7. What this research does not establish

- It does not confirm that any specific architecture is compliant. That requires counsel.
- It does not establish an RBI-defined bank-statement format — **there is none**.
- It does not resolve whether WhatsApp Business API media handling satisfies the 2025
  localisation requirement. That is an open question flagged in §4.
- Secondary legal commentary was the primary source here rather than the bare text of the
  Directions; specifics should be confirmed against the official RBI publication before any
  production claim.

---

## Sources

- [RBI (Digital Lending) Directions, 2025 — analysis, NLIU CBCL](https://cbcl.nliu.ac.in/contemporary-issues/analysing-rbis-digital-lending-directions-2025-a-positive-step-towards-responsible-lending/)
- [Digital Lending in India: a regulatory reset under the RBI Directions, 2025 — LexOrbis](https://www.lexorbis.com/digital-lending-in-india-a-regulatory-reset-under-the-reserve-bank-of-india-digital-lending-directions-2025/)
- [RBI Digital Lending Guidelines 2025 — Lawrbit](https://www.lawrbit.com/article/reserve-bank-of-india-digital-lending-directions-2025/)
- [The Digital Personal Data Protection Act, 2023 — MeitY (official text)](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf)
- [DPDP Act — data fiduciary obligations, s.8](https://www.dpdpa.com/dpdpa2023/chapter-2/section8.html)
- [Navigating India's DPDPA Rules — Securiti](https://securiti.ai/india-digital-personal-data-protection-act-dpdpa-rules/)
