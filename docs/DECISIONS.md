# Architecture & Product Decision Log

Format per decision: **Decision → Reason → Alternative considered → Tradeoff accepted.**
Decisions are append-only. Superseded entries are marked, not deleted.

---

## ADR-001 — Deterministic rules adjudicate; the LLM only assists

**Decision.** Validation verdicts are produced exclusively by deterministic, config-driven
rules. An LLM may normalise extracted fields from unfamiliar layouts and may rephrase
customer-facing messages, but it cannot set or change an outcome.

**Reason.** The product's value is a *trustworthy* verdict. A verdict that cannot be
explained, reproduced, or audited is worthless in a lending workflow. Deterministic rules
give identical outputs for identical inputs, produce evidence for every decision, and can
be reasoned about by Credit and Compliance.

**Alternative considered.** LLM-as-judge over the raw document, with rules as a sanity
check. Faster to build and handles format variation gracefully.

**Tradeoff accepted.** We give up some robustness to unseen bank layouts, and we must
maintain bank profiles by hand. In exchange we get reproducibility, auditability, and the
ability to state precisely why a document failed.

---

## ADR-002 — Local-first, pluggable extraction instead of Google Document AI

**Decision.** A `DocumentExtractor` interface with two implementations: `pdfplumber` for
native-text PDFs and a local OCR engine (Tesseract/PaddleOCR) for scanned PDFs. A Google
Document AI adapter is documented as a drop-in alternative but is not the default.

**Reason.**
1. **Data residency.** Document AI's specialised processors are not offered in
   `asia-south1`. Using them means Indian financial documents leave India — which directly
   contradicts the data-minimisation and residency posture this product argues for.
2. **Cost and setup.** A billed GCP project and credentials on every developer machine, for
   documents that are native-text PDFs where `pdfplumber` extracts near-perfectly.
3. **Demo reliability.** The MVP must run offline, at zero cost, on a clean machine.

**Alternative considered.** Google Document AI Bank Statement Parser (the brief's original
recommendation); AWS Textract.

**Tradeoff accepted.** Lower out-of-the-box accuracy on genuinely messy real-world
statements, and more bank-profile maintenance. Mitigated by the interface: switching to a
managed parser is a provider swap, not a rewrite.

**Known risk.** Testing only against PDFs we authored makes extraction look easier than
reality. Mitigation: the corpus must include materially different layouts plus a degraded
scan, so the OCR and low-confidence paths are genuinely exercised.

---

## ADR-003 — Database-backed job queue instead of Google Pub/Sub

**Decision.** A `jobs` table plus an in-process worker loop, behind a `JobQueue` interface.

**Reason.** For a single-service MVP, Pub/Sub adds emulators, IAM, and local-dev friction
without changing behaviour. A DB-backed queue gives async processing, retries, dead-lettering,
and — usefully — makes job state visible in the same store as the document state machine,
which the audit trail wants anyway.

**Alternative considered.** Google Pub/Sub (per the brief); Celery + Redis; FastAPI
`BackgroundTasks`.

**Tradeoff accepted.** Does not scale to high throughput or multi-worker fan-out without
care. Irrelevant at MVP volume; the interface allows Pub/Sub later without touching callers.
`BackgroundTasks` was rejected because jobs would not survive a restart.

---

## ADR-004 — Modular monolith

**Decision.** One FastAPI application, internally partitioned into `api / services /
validators / providers / domain / models / workers`.

**Reason.** The brief's own instruction, and correct. Microservices would add deployment and
observability cost with no benefit at this scale, and would obscure the product story.

**Alternative considered.** Service-per-concern (ingestion, extraction, validation).

**Tradeoff accepted.** Components scale together. Acceptable — module boundaries are
enforced by interfaces, so extraction could be extracted later if volume demanded it.

---

## ADR-005 — Local Docker Compose is the primary run target; cloud is optional

**Decision.** `docker compose up` runs Postgres + API + worker + frontend. GCP Cloud Run
deployment is a late, optional phase using a single service that serves both the API and the
built frontend.

**Reason.** A case-study demo must be runnable on demand, offline, at zero cost. Cloud SQL
bills continuously and has no free tier. Serving the frontend from the same origin as the
API also removes the CORS split that GitHub Pages + Cloud Run would introduce.

**Alternative considered.** Full GCP from day one (brief's original); GitHub Pages +
separate Cloud Run backend.

**Tradeoff accepted.** The "deployed and live" story arrives later, or not at all. Every
phase before it is faster.

---

## ADR-006 — `document_requirements` as a first-class entity

**Decision.** Model "this application owes a bank statement" as `document_requirements`.
Each uploaded file is a `documents` row — a *submission* against a requirement.

**Reason.** The primary metric is First-Time Document Clearance Rate =
`requirements cleared on the first submission / total requirements`. If a re-upload created
a new top-level document, the denominator would inflate with every correction and the
headline metric would be uncomputable — it would perversely *improve* as rework increased.

**Alternative considered.** Versioning documents in place (`documents.version`), per the
brief's original sketch.

**Tradeoff accepted.** One extra table and one extra join. Negligible, and it makes the
correction loop's semantics explicit rather than implied.

---

## ADR-007 — Balance continuity as the primary completeness check

**Decision.** Completeness is verified arithmetically: for every transaction,
`balance[i-1] ± amount[i] == balance[i]`; and across page boundaries, the closing balance of
page *N* equals the opening balance of page *N+1*. Supplemented by date monotonicity and
page-number continuity.

**Reason.** Page count alone proves nothing — a statement can have all its page numbers and
still be missing content, cropped, or partially exported. Balance arithmetic is the one
check a bank statement cannot pass by accident. It is cheap, deterministic, and fully
explainable to the customer ("the balance on page 7 doesn't carry into page 8").

**Alternative considered.** Page count vs. expected; header/footer "Page X of Y" parsing
only.

**Tradeoff accepted.** Requires reliable transaction-level extraction, which raises the bar
on the extraction stage. Accepted — that bar is worth clearing anyway. Where transactions
cannot be extracted reliably, the rule reports *not evaluated* and lowers rule coverage,
pushing the case toward REVIEW rather than producing a false verdict.

---

## ADR-008 — Integrity signals route to REVIEW, never to a verdict

**Decision.** Cheap deterministic tamper signals (PDF producer/creator metadata, incremental
revision count, native-vs-image text layer, font consistency, balance arithmetic breaks) are
collected and surfaced. They can only push a document to **REVIEW**. They can never produce
a rejection, and they are never described to the customer as suspicion.

**Reason.** Edited bank statements are a real risk in MSME lending, and these signals are
nearly free once the PDF is already parsed. But an automated fraud accusation against a
borrower — from a system with no ground truth — is both wrong and harmful. Human judgement
is the correct destination.

**Alternative considered.** Omitting integrity checks entirely (the brief's scope); or a
scored "authenticity" verdict.

**Tradeoff accepted.** Some false REVIEWs, which cost operations time. Acceptable: the cost
of a needless human check is far below the cost of either a false accusation or an accepted
forgery.

---

## ADR-009 — Explainable composite confidence, not an opaque score

**Decision.** Confidence is composed from (a) per-field extraction confidence weighted by
field criticality and (b) **rule coverage** — how many rules could actually be evaluated
against the data available. Two configurable thresholds define the decide/REVIEW boundary.
Operations sees the component breakdown, not just the number.

**Reason.** "Confidence: 97%" with no provenance is decoration. Rule coverage matters as
much as field confidence: a document where half the rules could not run is not "high
confidence" no matter how crisp the OCR was.

**Alternative considered.** Raw OCR confidence passthrough; a fixed per-rule confidence table.

**Tradeoff accepted.** More moving parts to tune, and the thresholds are initially an
**ASSUMPTION** rather than an empirically fitted value. Stated as such, and config-driven.

---

## ADR-010 — Real LLM with a deterministic fallback

**Decision.** An `LLMProvider` interface with an Anthropic implementation (`claude-opus-5`)
and a deterministic template-based implementation. The LLM is used for normalising
unrecognised bank layouts and for phrasing customer messages. The deterministic
implementation is used when no API key is present, and **all tests pass without a key**.

**Reason.** The brief asks for genuine AI-assisted normalisation, and it is the honest way
to handle layout variation without hand-writing a profile per bank. But the demo must never
depend on a network call, and the test suite must be reproducible.

**Alternative considered.** No LLM at all; or LLM as a hard dependency.

**Tradeoff accepted.** Two code paths to maintain, and output wording differs slightly
between them. Acceptable — reason codes and outcomes are identical in both paths, which is
the part that matters.

---

## ADR-011 — Prototype-grade auth: shared secret + signed URLs

**Decision.** Operations endpoints are gated by a shared secret. Document downloads use
short-lived signed URLs. There is no user identity, login, or role model.

**Reason.** The security section of the spec is meaningless if `/documents/{id}/download` is
openly enumerable. This is the minimum that makes the posture honest, without building an
auth system that proves nothing about the hypothesis.

**Alternative considered.** Full OIDC/JWT with roles; or no auth at all.

**Tradeoff accepted.** Not production-grade, and explicitly documented as such in the spec
and README. Real deployments would need per-user identity and an audit trail keyed to it.

---

## ADR-012 — SQLite by default for development; PostgreSQL for parity and deployment

**Decision.** The application's default database is file-backed SQLite. PostgreSQL is
available via `docker-compose.yml` and is the deployment target. All models use portable
column types defined in `app/db/base.py`; no module may import `postgresql.JSONB` or
`postgresql.UUID` directly.

**Reason.** Docker is not installed on the development machine this project was scaffolded
on, and ADR-005 committed to a demo that is *always runnable*. Requiring Docker Desktop
before a single test can run contradicts that. SQLite ships with Python, needs no daemon,
and supports everything the MVP's data model actually uses.

**Alternative considered.** Require Docker Desktop and run Postgres from the start
(ADR-005's original intent); or use an embedded Postgres binary.

**Tradeoff accepted.** SQLite and Postgres genuinely differ — JSON vs JSONB, weaker type
affinity, no concurrent writers, limited `ALTER TABLE`. Four mitigations:

1. Portable column types only, declared in one place.
2. `render_as_batch` in Alembic so migrations work on both.
3. Postgres available through compose, so parity can be verified on demand.
4. WAL mode and `check_same_thread=False`, so the in-process worker can read while the API
   writes — the one concurrency property this design actually needs.

**Supersedes** the part of ADR-005 that made Docker Compose the primary run target. Compose
is now optional and provides Postgres only.

**Honest caveat.** The compose file is **untested in this environment** for the reason
above. It is provided for parity work, not as a verified path.

---

## ADR-013 — Validation rules stay in YAML; no `validation_rules` database table

**Decision.** The rule catalogue and its thresholds live in
`backend/app/config/validation_rules.yaml`, loaded and type-validated at startup. The
`validation_rules` table sketched in the original brief is not built. Each
`validation_results` row records the `rules_version` that produced it.

**Reason.** A verdict must be reproducible and explainable months later. Rules in Git are
versioned, reviewable, diffable, and deploy atomically with the code that interprets
them. Rules in a table can be edited without review, leave no record of who changed a
threshold or why, and can drift between environments — so two documents could receive
different verdicts for reasons nobody can reconstruct. For a lending decision path that
is the wrong trade.

**Alternative considered.** A `validation_rules` table with an admin UI, per the brief's
data-model sketch. It buys runtime tuning without a deploy.

**Tradeoff accepted.** Changing a threshold requires a deploy. At MVP scale that is a
feature, not a cost: `recency_tolerance_days` is an explicit ASSUMPTION
(`VALIDATION_RULES.md` R-PER-002) and the first time it changes, it *should* go through
review. If runtime tuning is needed later, the table becomes an override layer over the
file rather than a replacement — the YAML stays the floor.

---

## ADR-014 — Failed processing routes to REVIEW, never to silence

**Decision.** A job whose retries are exhausted transitions its document to `IN_REVIEW`
with reason `PROCESSING_ERROR` and opens an Operations review. `IN_REVIEW` is reachable
from every non-terminal state to make this always possible.

**Reason.** The failure this product exists to eliminate is a customer who submits a
document and hears nothing. An infrastructure failure produces exactly that experience —
and it is *worse* than the original problem, because at least the old process eventually
noticed. REVIEW already means "the system cannot confidently decide", which covers "the
system could not finish".

**Alternative considered.** Leaving dead jobs in a dead-letter queue for an engineer to
find; or auto-failing the document to the customer as a FIX.

**Tradeoff accepted.** Operations sees cards that are our fault rather than the
document's, which inflates the manual-review rate with issues that are not document
defects. Acceptable, and the reason code keeps the two distinguishable in the metrics.
Auto-FIX was rejected outright: telling a customer to re-upload a perfectly good document
because our extractor crashed is both wrong and erodes trust in every other FIX.

---

## ADR-015 — A process-local password vault bridges the request and the worker

**Decision.** A PDF password submitted to `POST /documents/{id}/password` is placed in
`PasswordVault`: an in-process, single-use, TTL-bounded (5 minute) holding area. The
worker `take`s it — which removes it — decrypts in memory, and never writes the plaintext
anywhere. The password is never logged, never persisted, and **not hashed**.

**Reason.** Two requirements collide. Processing is asynchronous, so the password arrives
in an HTTP request but is needed moments later by a worker; and the password must not be
persisted. Something has to carry it across that gap.

Not hashing deserves saying out loud: Indian e-statement passwords are derived from a
date of birth, a customer ID, or an account number (`RESEARCH_BANK_FORMATS.md` §2). A
hash of an 8-digit DDMMYYYY value is brute-forced in seconds, so storing one provides no
protection while creating a stored secret and a piece of personal data we would then owe
a retention and deletion story for.

**Alternatives considered.**
1. *Process the document synchronously inside the password request.* Cleanest
   security-wise — the password never outlives the request. Rejected because it creates a
   second path to a verdict that bypasses the retry and dead-letter guarantees, and it
   blocks an HTTP request for the whole OCR pipeline.
2. *Persist the decrypted PDF and process it normally.* Rejected outright: it produces a
   plaintext financial document sitting outside the protection the bank applied.
3. *Store the password (encrypted or hashed) on the document row.* Rejected per the
   reasoning above.

**Tradeoff accepted.** Being process-local, the vault only works because the worker runs
in-process with the API (ADR-003/ADR-004). **Moving to out-of-process workers breaks
this**, and the correct fix then is to re-prompt the customer — not a shared cache, which
would mean passwords crossing a network and living in another system's memory. The TTL is
deliberately short enough to be a handoff rather than a cache. If the entry has expired or
the process restarted, the customer is asked again rather than the document being failed,
because nothing is wrong with the document.

**Verified, not asserted.** The no-leak guarantees are enforced by tests that were
mutation-checked: deliberately logging the password makes them fail. See
`tests/test_ingestion_api.py`.

---

## ADR-016 — RapidOCR for the scanned path, and the latency that comes with it

**Decision.** Scanned (image-only) PDFs are read with **RapidOCR** (ONNX Runtime),
selected by a router that prefers the native text layer whenever one exists. Measured
cost: **~20 seconds per A4 page at 150 dpi** on a 12-core laptop.

**Reason.** Tesseract needs a system binary, which breaks the "clone and run" promise on
a machine with nothing pre-installed — the development machine for this project had no
Tesseract, and requiring one would have made the OCR path untestable here. RapidOCR
installs from pip with bundled models and runs entirely locally, which is the property
ADR-002 was chosen for in the first place.

**Alternatives considered.** Tesseract via `pytesseract` (system binary); EasyOCR or
PaddleOCR (torch/paddle, hundreds of MB); Google Document AI (the residency problem
ADR-002 exists to avoid).

**Tradeoff accepted — measured, not estimated.** A 7-page scan takes ~5 minutes end to
end; a managed parser returns in seconds. Two things make it tolerable:

1. The OCR path is the **minority case**. Bank-issued e-statements carry a native text
   layer, and those extract in **under 1.2 seconds** for an 11-page document.
2. **Page-level parallelism was tried and is counter-productive** — measured at **0.61x**,
   i.e. slower. ONNX Runtime already saturates the cores, so multiple engines thrash.
   Recording this matters: it is the obvious optimisation, and it does not work.

**The mitigation that is also correct product behaviour.** After probing two pages, if
more than 20% of reads fall below the confidence floor, extraction **stops**. On the
degraded corpus scan this cuts 11 pages to 2 — 68 seconds instead of ~4 minutes. The
outcome was going to be REVIEW either way, so spending three more minutes to extract text
we already know we cannot trust only makes the customer wait longer for the same answer.

**Result.** On the clean scanned statement, OCR reconstructs **437 of 437 transactions
with zero balance breaks** — identical to the native path, through the same parser.

---

## ADR-017 — One parser for both text sources

**Decision.** Native text (pdfplumber) and OCR (RapidOCR) both emit the same
`RawExtraction` shape — positioned cells with per-cell confidence — and a single
geometric parser normalises either into the canonical statement.

**Reason.** Two parsers would drift, and the OCR path would quietly become the
less-tested one. More importantly, a shared parser makes the two sources *comparable*:
when the OCR path produces a different answer, that difference is attributable to read
quality rather than to a different code path.

The parser is geometric rather than textual — it locates column *labels* in the header
band, derives boundaries from their positions, and assigns every cell below by where it
sits. Regex over flattened page text cannot survive multi-line narration, which the
research calls the single biggest extraction hazard (§3.3): once rows stop mapping
one-to-one onto transactions, line-based parsing silently merges or drops them.

**Alternative considered.** A parser per bank layout, or per text source.

**Tradeoff accepted.** Geometric parsing needs positional data, so any future extractor
must supply coordinates — a managed parser that returns only flat text would not slot in
unchanged. Worth it: the approach handles all five corpus layouts, including the
wrapped-narration one, with **100% transaction extraction and exact balance
reconciliation** on every valid document.

**Bank profiles are synonym sets, not templates.** Research established there is no
public per-bank column specification and no RBI-defined format, so the profile lists
*synonyms per concept* ("Narration" / "Particulars" / "Transaction Remarks"). A layout
nobody anticipated still parses if its headers use recognisable words, and no blocking
rule depends on a bank-specific detail.

---

## ADR-018 — The confidence score must not veto structural verdicts

**Decision.** Each reason code carries a `composite_gated` flag. When it is `false`, the
composite confidence score cannot downgrade that verdict. It is `false` for every
file-level code and for `DOC_TYPE_MISMATCH`, `READABILITY_NO_TEXT` and
`EXTRACTION_LOW_CONFIDENCE`.

**Reason.** This surfaced while wiring the engine, and it is a genuine flaw in the
original confidence model. The composite score measures **how well we read a bank
statement** — weighted field confidence plus rule coverage. Apply it to a GST
certificate and it is near zero, *because there was no bank statement to read*. Gating on
it would turn the most confident, most actionable verdict the system can produce ("this
is not a bank statement — please send one") into a needless manual review, purely because
the document contained none of the fields it was never going to contain.

The general principle: a confidence score computed from artefacts of *class A* says
nothing about a document of *class B*. Structural findings — the file will not open, the
document is a different kind of thing — are established by their own evidence and must
not be vetoed by a measure that does not apply to them.

**Alternative considered.** A separate confidence model per document class. Correct, and
far more machinery than an MVP with one document type can justify.

**Tradeoff accepted.** One more flag per reason code, and a reviewer must understand why
two failing rules with similar confidence resolve differently. Mitigated by keeping the
flag in the same catalogue as the outcome and actionability, where its effect is visible
next to what it affects.

---

## ADR-019 — A downgraded FIX is reported as low confidence, not as the rule that fired

**Decision.** When a blocking rule fails but the composite score is below
`fix_threshold`, the outcome becomes REVIEW with primary reason
`EXTRACTION_LOW_CONFIDENCE`, and the rule that actually fired is recorded as a secondary
reason plus a `downgraded_from` entry in the evidence.

**Reason.** Consider the degraded scan: nine of eleven pages were unreadable, and the
readability rule fires. Reporting `READABILITY_POOR_SCAN` as the primary reason would be
accurate but misleading — it implies we know the document is the problem. What we
actually know is that **we could not read it well enough to decide**, which is a
different statement and the honest reason a human is being involved.

It also keeps the failure-reason distribution meaningful. If every low-confidence case
were filed under whichever rule happened to fire first, the metric would suggest a
document-quality problem where the real story is extraction confidence.

**Alternative considered.** Keeping the firing rule as primary and adding a confidence
flag alongside it.

**Tradeoff accepted.** The specific finding moves one level down in the payload, so
Operations reads the secondary code to see what the engine suspected. Acceptable: the
Operations card shows both, and the primary reason answers the question a reviewer
actually has — *why is this on my desk?*

---

## ADR-020 — Customer messages are deterministic templates, not LLM-generated prose

**Decision.** Every customer-facing message is rendered from a template populated with the
firing rule's own **evidence**. The LLM is not used to write, rewrite or "soften" them.
The one place it may contribute is naming an unrecognised document type inside an
otherwise fixed message — a detail, not the instruction.

**Reason.** The brief lists "customer-friendly explanation of identified issues" as an
AI-assist use, and that is a reasonable instinct. But these messages have three
properties that argue against paraphrase:

1. **They must quote exact values.** "01 May – 31 Aug 2026" is what makes the message
   actionable. A paraphrase that drops or rounds a date produces a message the customer
   cannot act on, and we would not know it had happened.
2. **They must be reproducible.** The same document must produce the same instruction
   every time — for testing, for support ("what did we tell them?"), and because the
   message is the visible half of a decision recorded in an audit trail.
3. **They are the only thing standing between the product and "there was a problem with
   your document."** A generative step that occasionally hedges would undo the single
   clearest rule in the spec (§9).

Rendering from evidence also guarantees that the customer message and the Operations card
describe the same finding: both read the same dict.

**Alternative considered.** LLM phrasing with the template as a fallback; or LLM
post-editing for tone.

**Tradeoff accepted.** The messages are less varied and will not naturally adapt to a
customer's language — real localisation would need translation, which is a better-scoped
use of a model than free composition, and is not in MVP scope. This is a narrower use of
the LLM than the brief suggested; the reasoning is above, and the seam is there if the
tradeoff is judged differently later.

---

## ADR-021 — Accepting a review still counts as first-time clearance

**Decision.** When Operations accepts a document that was routed to REVIEW, the
requirement is cleared **and** `first_time_cleared` stays true if it was the first
submission.

**Reason.** First-Time Document Clearance Rate measures *the customer's experience of
rework*. On an accepted review the customer sent a usable document on their first
attempt; the fact that a human had to look at it is **our** uncertainty, not their
mistake. Counting it against them would turn the headline metric into a measure of our
confidence rather than their friction — and it would improve automatically whenever we
made the engine more aggressive, which is precisely the wrong incentive.

Manual review rate is tracked separately (`PRODUCT_SPEC.md` §24), which is where the cost
of our uncertainty belongs.

**Alternative considered.** Treating any human touch as a failure of first-time
clearance.

**Tradeoff accepted.** The headline number does not, by itself, reveal how much human
effort a cohort consumed. Reading it alongside manual-review rate does — and keeping the
two separate is what makes each of them mean something.

---

## ADR-022 — The customer composer has no free-text input

**Decision.** The customer can do exactly two things: attach a document, or type a
password when one is asked for. There is no message box.

**Reason.** `PRODUCT_SPEC.md` §9 calls for a *structured document-verification
assistant*, not an open-ended chatbot, and an empty text field is a promise. Offering one
invites "why was it rejected?", "can you check it again?", "I'll send it tomorrow" — an
entire conversation the product cannot hold and was never meant to. Every such message
would be either ignored or answered by something that would have to be built, and the gap
between the affordance and the behaviour is exactly where trust is lost.

The constraint also keeps the interaction honest: every customer action maps to a state
transition the backend already models.

**Alternative considered.** A text box routing unrecognised messages to Operations.

**Tradeoff accepted.** A customer with a question has no way to ask it here. In the
intended deployment that is correct — this is one automated number in a journey where
Sales is already the human contact. Were the channel ever the only contact point, the
right answer would be a routed handoff to a person, not a chatbot.

---

## ADR-023 — Demo sample submission goes through the real pipeline

**Decision.** The UI's sample-document buttons post a document *id*; the backend reads
that file from the corpus and passes it through the same `ingest()` path as a browser
upload — same magic-byte check, same storage, same queue, same worker. The endpoints are
gated on `demo_mode`, off outside local development.

**Reason.** A demo that injects a pre-computed result proves nothing, and worse, it hides
exactly the breakages a demo is supposed to surface. A test asserts that a sample
submission and a real upload of the same file produce an identical SHA-256 and the same
verdict, so the shortcut cannot silently diverge from the real path.

The gate matters because the endpoint reads from a directory. The corpus is synthetic by
construction, but "the directory only contains safe files" is an assumption that ages
badly, so the path is also resolved and checked against the corpus root.

**Alternative considered.** Shipping the corpus into the browser bundle and uploading it
back; or making the demonstrator use a file picker for every scenario.

**Tradeoff accepted.** A demo-only code path exists in the backend. It is small, gated,
tested, and the alternative — 19 trips through a file dialog during a walkthrough — would
make the product look clumsier than it is.

---

## ADR-024 — A superseded document withdraws its open review

**Decision.** When a newer submission supersedes an in-flight one, any `OPEN` review on
the superseded document is moved to a new `WITHDRAWN` status, audited as
`REVIEW_WITHDRAWN`, with no reviewer action recorded.

**Reason.** Found by clicking through the Operations UI, not by reasoning about it. A
customer who sends a second document while the first is with Operations supersedes it —
which is correct. But the review stayed `OPEN`, pointing at a document that no longer
represented the requirement. A reviewer would open the card, make a decision, and get an
error, because `resolve()` refuses to act on a document that is not `IN_REVIEW`.

The quieter harm is worse than the error. The queue looked busier than the work actually
was, and because it is worked oldest-first (deliberately, to avoid stranding cases), the
dead cards floated to the top — so the ordering that exists to protect the longest-waiting
customer would have served un-actionable work first.

`WITHDRAWN` rather than `RESOLVED` because nobody decided anything; the question stopped
being worth answering. Recording it as a resolution would corrupt the manual-review
metrics with decisions that were never made.

**Alternative considered.** Leaving the review open and letting `resolve()` fail; or
auto-resolving it as `REQUEST_NEW_DOCUMENT`.

**Tradeoff accepted.** A reviewer part-way through assessing a document can have it
disappear from under them. That is the honest outcome — the document they were judging is
no longer the one that matters — but a production queue should tell them so rather than
silently removing the row.

---

## ADR-025 — The operations credential is typed, never bundled

**Decision.** The Operations and Sales views gate on a secret the operator types, held in
`sessionStorage` for the tab's lifetime. It is never present in the built bundle.

**Reason.** A secret compiled into frontend code is in source control and served to every
visitor, which would make `PRODUCT_SPEC.md` §12's "secrets outside frontend code" false
while appearing to satisfy the access control it protects.

**Alternative considered.** No gate at all in the demo; or an environment variable baked
in at build time (which is the same thing as bundling it).

**Tradeoff accepted, and stated on the gate screen itself.** `sessionStorage` is readable
by any script on the page, so an XSS bug would leak the secret — and one shared secret
means review decisions carry a name typed into a box rather than an authenticated
identity. This is ADR-011's prototype posture made visible to the person using it: the
production shape is per-operator authentication returning an HttpOnly session cookie.

---

## ADR-026 — A specific finding requires the confidence to make it

**Context.** A real Canara Bank statement, submitted as phone screenshots, produced
`REVIEW / COMPLETENESS_BALANCE_BREAK` at a composite confidence of 0.18. Six transactions
had been parsed out of nine pages and none of them was complete. The system had, in effect,
announced that a balance chain it had never read did not reconcile.

Two separate defects combined to produce it.

First, `_resolve` checked the confidence gate only on the path to FIX. Codes whose default
outcome was REVIEW short-circuited it, on the reasoning that REVIEW is the cautious
destination and caution needs no justification. That reasoning is wrong. **Naming a finding
is a claim**, and the claim needs evidence whatever outcome follows it. A reviewer handed
"the balance does not reconcile" goes looking for a defect in the customer's document; the
actual problem was ours, and the specific reason code hid it.

Second, R-CMP-002 would evaluate a chain of any length. Two transactions are enough to
produce a break, and a partial read produces them readily — they are artefacts of our
extraction, not defects in the document.

**Decision.**
1. The confidence gate in `validation_engine._resolve` runs before the REVIEW-default
   branch, so any gated code lacking evidence becomes `EXTRACTION_LOW_CONFIDENCE`.
2. R-CMP-002 reports `NOT_EVALUATED` below `min_transactions_for_balance_check` (10)
   complete transactions.

**Alternative considered.** Lower the thresholds so weak reads reach REVIEW with their
specific code intact. Rejected: it treats the symptom. The problem is not which outcome was
chosen but that the *reason* was asserted without support.

**Tradeoff.** Some genuine balance breaks on short statements now arrive as
`EXTRACTION_LOW_CONFIDENCE` instead — a vaguer card for the reviewer. That is the correct
direction to be wrong in: a reviewer told "we could not read this well" investigates from
scratch, whereas one told "the balance is broken" investigates a claim that may be false.

---

## ADR-027 — Readability is judged over the pages we read, not the pages in the document

**Context.** `readable_page_ratio` divided readable pages by the document's *total* page
count. Once OCR could stop early (ADR-028), a clean 9-page document read to page 3 scored
0.33 and failed R-RED-001 as a poor scan — telling a customer their perfectly legible
statement was unreadable, because of a decision we had made about our own time budget.

**Decision.** The denominator is the pages read. How much of the document we got through is
`page_coverage`, which already exists, already feeds the composite, and means something
different.

**Alternative considered.** Suppress R-RED-001 when `aborted_early` is set. Rejected as a
special case papering over a conflation: the two numbers were always different facts, and
early abort only made the confusion visible.

**Tradeoff.** A document truncated by the page cap no longer fails readability. It still
reaches REVIEW through reduced coverage, which is the honest route — a 40-page statement is
not a poor scan.

---

## ADR-028 — OCR stops when the *structure* is futile, not only when the reading is poor

**Context.** The document above took 91 seconds to OCR nine pages. The existing early abort
fires on low read confidence; this document OCR'd at 0.82 mean confidence, so it read every
page. But the answer was determined by page three: a table had been found, rows had been
parsed, and not one carried both an amount and a balance. Pages four to nine repeated the
same layout, cost a minute, and changed nothing.

**Decision.** After `ocr_structural_probe_pages` (3), run the real transaction parser over
the pages read so far. If a table was found *and* rows were parsed *and* none is complete,
stop. Measured effect on the document that prompted this: 91s → 30s, reading 3 pages of 9.

The probe runs the production parser rather than an approximation, because the whole value
of an abort is that its conclusion matches what the full run would have concluded.

**Alternatives considered.** Lower the DPI — rejected, it trades accuracy on small print for
a linear saving. Parallelise pages — already measured at 0.61× (ONNX Runtime saturates the
cores itself). Give up on local OCR — rejected, it is the data-residency premise of ADR-002.

**Tradeoff.** A statement whose first three pages genuinely lack amounts but whose later
pages carry them would be cut short. The conditions are deliberately conservative — rows
must have been *parsed* first, so a cover page or a late-starting table reads on — and the
outcome is a FIX asking for the bank's PDF, which resolves it in one round trip either way.

---

## ADR-029 — An unrecognised column is a column, not a gap

**Context.** The deepest defect behind the wrong verdict, and the one that made everything
above necessary. The statement's header read `DATE | Dr/Cr | AMOUNT | BALANCE`. We have no
synonym for `Dr/Cr`, so it matched nothing, and the header-position logic simply skipped it.
Column boundaries were then drawn between the labels we *did* recognise, which put the
`Dr/Cr` region inside the date column. Every row's date read as `24/04/2025 Dr`, failed to
parse, and was discarded as a continuation line. Six transactions survived out of roughly
seventy.

The document was then, accurately, described by the system as nearly empty — and the
validators reported that emptiness as a defect in the customer's statement.

**Decision.** Two changes, both general rather than specific to this layout:
1. Every unmatched cell in the header row becomes an `UNKNOWN_COLUMN`. It claims its own
   x-range and its contents are dropped. An unknown column is far less damaging than a
   wrong one.
2. `_date_from` falls back to the first parseable token in the date column, mirroring the
   tolerance `_amount_from` already had. One stray token must not cost a row.

**Effect on the same document:** 6 parsed transactions → 23 (in 3 pages rather than 9), and
the bank identified correctly.

**Alternative considered.** Add `Dr/Cr` to the synonym list. Done as well, but rejected *as
the fix*: the next unrecognised label would reproduce the bug exactly, and there is no
authoritative list of Indian column headers to enumerate against
(`RESEARCH_BANK_FORMATS.md` §6).

**Tradeoff.** A header row containing stray non-label text (a page number at the far right,
say) now creates a spurious column that swallows anything beneath it. Measured against the
corpus: no change to any of the 19 documents.

---

## ADR-030 — Silence about supersession is indistinguishable from lying

**Context.** The same tester, waiting on a slow document, clicked a sample instead. Their
submission was superseded — correctly, per ADR-013 — and the sample's verdict appeared in
the thread: wrong account type, unfamiliar dates. They read it as a verdict on their own
statement and reported that the system was badly wrong. Every word of the message was true
about a file they had not sent.

**Decision.** Supersession sends the customer a message naming both documents. Separately,
`record_message` stamps the filename and submission index into every message's context, so
the UI can attribute a verdict to its document; the chat shows the attribution once a
thread holds more than one.

Stamping happens in `record_message` rather than at each call site specifically so that no
future message can be written without it.

**Alternative considered.** Block new submissions while one is in flight. Rejected: an
impatient re-send is normal behaviour, not an error, and refusing it is a worse experience
than explaining it.

**Tradeoff.** One extra message in the thread on a re-send. Cheap.

---

## ADR-031 — A bank is identified from the masthead, never from the body

**Context.** The Canara Bank statement was reported as "HDFC Bank". `identify_bank` searched
the whole of page one for any known bank name; the page was full of UPI narrations naming
counterparties' banks, and Canara was not in the table at all, so the matcher fell through
to the first name it did recognise.

A bank statement is one of the few documents that reliably contains *other* banks' names —
every NEFT, IMPS and UPI line carries one. Searching the body makes the busiest account the
least identifiable.

**Decision.** Three changes:
1. The name search is bounded by the transaction-table header row: only the letterhead above
   it is considered. Bounded by structure rather than a row count, because a fixed count is
   a guess and the table header is the actual boundary.
2. The IFSC search is bounded identically. It is tried *first*, so an unbounded one would
   not merely compete with the printed name — it would silently outrank it.
3. Twelve public-sector and mid-size private banks added to `banks.yaml`. An absent entry is
   not a neutral gap: it is an active source of wrong answers.

**Tradeoff.** A statement that prints its IFSC only in a footer is no longer identified by
it. Identification is presentational — no blocking rule depends on it — so a missing bank
name costs far less than a wrong one.

---

## ADR-032 — A credential the browser cannot send must not reach `fetch`

**Context.** Testing the prototype with a rotated operations secret, both gated tabs died
with:

> Failed to execute 'fetch' on 'Window': Failed to read the 'headers' property from
> 'RequestInit': String contains non ISO-8859-1 code point.

The secret had been pasted, and the paste carried an invisible character —
a zero-width space is the usual culprit when a credential is copied out of formatted
text. Header values must be Latin-1, so `fetch` rejected it.

Three separate weaknesses turned that into a dead end.

1. `opsHeaders()` put whatever was in `sessionStorage` straight into a header. No
   validation anywhere between the operator's keyboard and the browser's header parser.
2. `fetch` rejects an unsendable header by throwing a `TypeError` **before the request
   exists**. Every error path in Operations and Sales is written around `ApiError` with a
   status — a transport-layer throw matches none of them, so the "bad credential, clear
   it and return to the gate" recovery never ran.
3. Sales/LOS had no Lock control. Operations has had one since Phase 10; this tab was
   simply missed. With the secret stuck and no way to clear it, the tab was unusable
   until the browser tab was closed.

The reported symptom — "the Refresh and − buttons don't work" — was all three: Refresh
re-ran the same failing fetch, and the "−" was not a button at all but the queue-count
badge showing its unknown-value placeholder, because the load never completed.

**Decision.**

1. `isHeaderSafe()` gates the value in `opsHeaders()`. An unsafe secret is **cleared and
   omitted**, degrading the failure to a plain 401 — the one failure every caller already
   knows how to recover from. One change at the source fixes both tabs.
2. `normaliseSecret()` strips zero-width and byte-order marks and trims. The operator did
   not type those characters, cannot see them, and would have no way to find them; fixing
   it silently is kinder than reporting it.
3. `OpsGate` validates on submit and explains the rejection in terms of the likely cause
   ("usually something picked up by copying from formatted text. Try typing it instead"),
   rather than repeating the browser's message.
4. Sales/LOS gets a Lock control, for parity and so the tab is never a dead end.

**Alternative considered.** Catch the `TypeError` in each page's error handler and treat
it as an auth failure. Rejected: it spreads knowledge of a `fetch` implementation detail
across every caller, and leaves the unsendable value in storage to fail again on the next
request. Validating at the point the value becomes a header is the narrower fix.

**Tradeoff.** A secret that legitimately contains non-Latin-1 characters can no longer be
used. That is a real restriction and the right one — such a secret could never have been
sent in this header anyway; the difference is that the operator now finds out at the gate
instead of from an exception three screens later.

**The uncomfortable part.** This product exists to replace unhelpful failure messages with
specific, actionable ones. Its own operator console failed with a raw browser exception
and no way to recover. Worth remembering that the standard has to be applied inward as
well as outward.

**Known gap:** the frontend has no test runner, so `normaliseSecret` and `isHeaderSafe` —
both pure and trivially testable — are covered only by manual verification. The backend's
392 tests have no counterpart here. Worth closing in Phase 12.
