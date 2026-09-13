# PROGRESS

**Project:** MSME Document Collection & Pre-Validation Layer (MVP)
**Last updated:** 2026-09-12

---

## Completed

### Phase 0 — Product Contract ✅
- `docs/PRODUCT_SPEC.md` (v1.1) — scope, users, outcome contract (PASS/FIX/REVIEW), full
  reason-code catalogue, the FIX-vs-REVIEW decision boundary, customer-experience
  principles, 7 acceptance scenarios, non-functional and security requirements, and an
  explicit list of what the MVP does *not* prove.
- `docs/DECISIONS.md` — ADR-001 … ADR-011, each with decision / reason / alternative /
  tradeoff.

**DoD met:** all 7 scenarios have a defined expected outcome and primary reason code; the
FIX-vs-REVIEW boundary is written down rather than left to implementation judgement.

### Phase 1 — Research ✅
- `docs/RESEARCH_BANK_FORMATS.md` — evidence for the 6-month current-account requirement;
  e-statement encryption and per-bank password conventions (including where sources
  **conflict**); the common field model; variation axes; the lending-relevant field subset;
  document-integrity findings.
- `docs/RESEARCH_REGULATORY.md` — RBI Digital Lending Directions 2025 (India-only storage,
  24-hour foreign-processing deletion, need-based collection, consent audit trail), DPDP Act
  2023 obligations, password handling, WhatsApp channel risk, LLM constraints.
- `docs/VALIDATION_RULES.md` (v1.0) — 17 rules across file / document type / period /
  completeness / readability / integrity / identity, each evidence-tagged; the composite
  confidence model; outcome resolution logic; the full YAML config; and an explicit list of
  rules deliberately *not* built.

**DoD met:** every rule traces to a research line or is explicitly tagged **ASSUMPTION**.

#### Findings that changed the design

1. **RBI Digital Lending Directions 2025 require borrower data to be stored in India**, with
   foreign-processed data deleted from foreign servers within 24 hours. This converts
   ADR-002 (not using Google Document AI's US/EU-only processors) from a cost argument into
   a **regulatory** one.
2. **FlexiLoans publicly requires "last 6 months of bank statement of current account."**
   The MVP's core requirement is VERIFIED, not invented.
3. **Running-balance reconciliation is an industry-standard integrity check**, named across
   bank-statement-analysis sources — validating ADR-007 on evidence rather than convenience.
4. **No public source specifies statement *recency*.** The 35-day tolerance (R-PER-002) is
   an explicit ASSUMPTION and the weakest-evidenced rule in the product.
5. **Password conventions conflict between sources and vary by delivery channel** within a
   single bank. The product must never derive or guess a password — only prompt.
6. **`COMPLETENESS_DATE_GAP` reclassified** from a blocking FIX to non-blocking integrity
   signal R-INT-004: a month with no transactions is legitimate for a dormant account.
   `PRODUCT_SPEC.md` amended to v1.1.

### Phase 2 — Scaffolding & development environment ✅
- `backend/` — FastAPI app factory, typed settings (`pydantic-settings`), portable
  SQLAlchemy base, engine/session management, Alembic wired to read the DB URL from the
  environment (never from `alembic.ini`), `/health` (liveness) and `/ready` (checks DB +
  rule config).
- `backend/app/config/validation_rules.yaml` — Phase 1's rules as executable config, plus
  `rules.py`, a strict typed loader that **rejects a malformed config at startup** rather
  than silently falling back to defaults.
- `frontend/` — React 19 + TypeScript (strict) + Vite 7 + Tailwind 4, with a `/api` dev
  proxy so the frontend never holds a backend hostname or secret.
- `README.md`, `.gitignore`, `.env.example` (both sides), `docker-compose.yml` (Postgres
  parity only), `.claude/launch.json`.

**Verified, not assumed:**
- `pytest` — **16 passed**.
- `ruff check` — clean; `ruff format --check` — 21 files already formatted.
- `npm run build` — clean TypeScript build.
- Backend served on :8000; `/ready` returns `{"database":"ok","validation_rules":"ok (v1,
  bank_statement)"}`.
- Frontend loaded in a browser at :5173 and **rendered live backend state** — the wiring
  is confirmed end to end, not inferred.

**DoD met:** a clean clone runs the stack with `pip install -e ".[dev]"` + `npm install`.

#### Tests worth noting
The Phase 2 suite is not filler. It pins the product contract:
- documented thresholds in `VALIDATION_RULES.md` §9 must equal the shipped config, so doc
  and code cannot drift;
- `pass_threshold >= fix_threshold` is asserted (ADR-009);
- confidence weights must sum to 1.0;
- reason-code priority must stay causally ordered;
- `reference_date_source` must not be wall-clock, or verdicts would change over time;
- secrets must never appear in `/ready` output;
- **the suite passes with no Anthropic API key present** (ADR-010).

### Phase 3 — Synthetic document corpus ✅
- `corpus/generator/` — `money` (integer paise, Indian digit grouping), `ledger`
  (arithmetically exact transaction generation), `profiles` (5 bank layouts), `render`
  (ReportLab + GST certificate), `degrade` (defect injection), `build` (CLI + manifest).
- **19 documents** covering all 7 acceptance scenarios plus account-type, identity and
  integrity cases, with a machine-readable `manifest.json` recording the injected defect,
  the expected outcome, and per-document ground truth.
- `corpus/README.md` — what each document proves, the manifest schema, design constraints.
- Deterministic: same `--as-of` + `--seed` reproduce the corpus byte-for-byte. Periods are
  derived from `--as-of`, so the corpus never goes stale.

**Verified:** `pytest` — **57 passed** (41 new corpus tests); `ruff check` clean across
backend and corpus; every PDF rendered and **visually inspected**.

#### Two controls that make the corpus honest
`scanned_clean_hdfc` (clean 300 dpi image-only PDF) and `integrity_edited_producer_sbi`
(one weak integrity signal) are expected to **PASS**. Without them the corpus could not
distinguish a system that reasons from one that is merely suspicious of anything unusual.

#### Four defects found by looking at the output rather than trusting it
1. **The column header band painted over the last header row**, silently deleting the
   *Statement Period* line from every statement with four or more header fields — which
   would have made valid HDFC documents look like they had no period at all. Header layout
   is now derived from the profile instead of a fixed offset.
2. **Pages were half empty and transaction volume was unrealistically low.** Rows per page
   are now computed from available space, and volume raised to 58–86 transactions/month.
3. **Multi-line narration never actually wrapped**, so the hardest extraction case
   (research §3.3 calls it the single biggest hazard) was not being exercised at all.
   Kotak now emits full-length UPI/NEFT narration that genuinely wraps — and the row
   striping had to be reordered, because it was painting over the wrapped second lines.
4. **The degraded scan was too legible** to justify a low-confidence verdict. Recalibrated
   with JPEG re-encoding artefacts — then, on the first attempt, *over*-corrected into
   illegibility, which would have tested `READABILITY_NO_TEXT` (a FIX) instead of
   `EXTRACTION_LOW_CONFIDENCE` (a REVIEW). Now tuned between both failure modes.

#### Two findings that changed the design
1. **"Corrupt" is a property of a parser, not of a file.** Truncating a PDF does not
   reliably make it unopenable — parsers rebuild damaged xref tables, recovery is *not*
   monotonic in how much survives, and parsers disagree (PyMuPDF recovered truncations
   pypdf rejected). A hand-tuned truncation fraction would break on the next regeneration.
   `corrupt_file()` now keeps the `%PDF-` signature (so the file reaches the *parse* check,
   not the *type* check) and replaces the body with deterministic noise — and **asserts at
   build time** that both parsers reject it.
2. **Keyword matching cannot classify a document.** The GST certificate contains
   "Particulars of Approving Authority", and *Particulars* is an Axis statement column
   label. R-DOC-001 must reason about table structure, not terms.

#### Contract change
`FILE_PASSWORD_REQUIRED` was not in the reason-code catalogue, because it is a **gate**,
not a failure. Config now separates `gate_reason_codes` from `primary_reason_priority`,
and a test asserts every code the corpus expects is one the engine can actually emit —
in both directions.

### Phase 4 — Domain core ✅
- **8 tables** — `applications`, `document_requirements`, `documents`,
  `document_extractions`, `validation_results`, `reviews`, `document_events`, `jobs`;
  one Alembic migration, applied and verified.
- **State machine** (`app/domain/`) — explicit closed transition table; illegal
  transitions raise rather than silently corrupting state.
- **Transition service** (`app/services/document_state.py`) — the *only* place
  `document.status` is assigned, which makes three guarantees structural rather than
  conventional: illegal transitions raise, an audit event is appended for every
  transition, and the requirement status is recomputed in the same transaction.
- **Job queue** (`app/providers/queue.py`) — `JobQueue` protocol + `DbJobQueue`, with
  dedupe, exponential backoff, dead-lettering and stale-lease reclamation.
- **Worker** (`app/workers/`) — runs in-process with the API, verified booting alongside it.
- **Audit** (`app/services/audit.py`) — every event scrubbed on the way in: secret-shaped
  keys redacted, long digit runs masked to their last four.

**Verified:** `pytest` — **109 passed** (52 new); `ruff` clean; migration applied; API
boots with the worker thread running (`run_worker: true` in `/ready`).

#### Design decisions worth noting

**Claiming uses a conditional UPDATE, not `SELECT ... FOR UPDATE SKIP LOCKED`.** SKIP
LOCKED is better on PostgreSQL but does not exist on SQLite, and a queue that behaves
differently on the dev database than in deployment produces bugs that only appear in
production. The conditional update is portable and still race-free.

**The requirement's status is derived from its submissions, never maintained separately**,
so the obligation and its submissions cannot drift apart. The interesting case is
NEEDS_FIX: the *submission* is finished, but the *requirement* is now awaiting the
customer — which is exactly the state the correction loop and the pendency metric need.

**`first_time_cleared` is frozen at clearance rather than derived on read**, because
documents are purgeable under the retention policy and a purge would take the submission
index with it — silently breaking the headline metric long after the fact.

**Supersession.** Customers re-send while the previous attempt is still processing;
without this, two verdicts would race and the earlier one could land last.

#### Two decisions that go beyond the brief
- **ADR-013 — no `validation_rules` table.** Rules stay in version-controlled YAML.
  A threshold edited in a database leaves no record of who changed it or why, and can
  drift between environments — so two documents could get different verdicts for reasons
  nobody can reconstruct. Each result records its `rules_version`.
- **ADR-014 — failed processing routes to REVIEW, never to silence.** A customer who
  submits a document and hears nothing is the exact failure this product exists to
  remove, and an infrastructure failure produces precisely that. `IN_REVIEW` is therefore
  reachable from every non-terminal state. Auto-issuing a FIX was rejected: telling a
  customer to re-upload a good document because our extractor crashed erodes trust in
  every other FIX.

#### Fixed along the way
Autogenerated migrations referenced `postgresql.JSONB` and `Text` without importing them
(a consequence of the portable JSON variant), so the first migration would have failed on
a clean deploy. The Alembic template now carries both imports. `alembic/env.py` also now
honours an explicitly supplied URL, so migrations can target a named database instead of
silently using whatever the environment points at.

### Phase 5 — Ingestion, file checks, password flow ✅
- **Storage** (`providers/storage.py`) — `StorageProvider` protocol + local
  implementation. Opaque generated keys, never derived from the uploaded filename;
  resolved paths are refused if they escape the storage root.
- **Signed URLs** (`services/signed_urls.py`) — HMAC tokens bound to one document id,
  with the expiry *inside* the signed payload, verified in constant time.
- **File checks** (`validators/file_validator.py`) — R-FILE-001…004: magic bytes, size,
  empty, parseability, encryption detection.
- **Password handling** (`services/password.py`) — `PasswordVault` plus in-memory unlock.
- **Pipeline** (`services/pipeline.py`) — state-aware handler; a document parked at
  `AWAITING_PASSWORD` resumes days later through the same job type.
- **API** — 11 endpoints: applications, upload, status polling, password submission,
  ops-gated download URL minting, token-gated download.

**Verified:** `pytest` — **158 passed** (49 new); `ruff` clean; and a **live run against
the real server**: encrypted statement → `AWAITING_PASSWORD` → password → `EXTRACTING`;
corrupt file → `NEEDS_FIX` / `FILE_CORRUPT` with a specific next action.

#### Honest scope note
Phase 5's definition of done said "reach EXTRACTED". It cannot: extraction is Phase 6.
A good document now reaches **`EXTRACTING`** and parks there. Acceptance scenario 3
(password) and scenario 5 (corrupt/unusable) are proven as far as this phase goes.

#### The design problem this phase surfaced (ADR-015)
Processing is asynchronous, so the password arrives in an HTTP request but is needed
moments later by the worker — while the rules say it must never be persisted. Something
has to carry it across that gap. The answer is a process-local, single-use, 5-minute
vault; the worker `take`s it, which removes it.

**It is deliberately not hashed.** Indian e-statement passwords are derived from a date of
birth, customer ID or account number, so a hash of an 8-digit DDMMYYYY value is
brute-forced in seconds — no protection, while creating a stored secret and a piece of
personal data we would owe a deletion story for.

**Known limit:** the vault is process-local, so it only works because the worker runs
in-process. Moving to out-of-process workers breaks it, and the right fix then is to
re-prompt the customer — not a shared cache, which would put passwords on a network.

#### A vacuous security test, caught by mutation testing
All 22 end-to-end tests passed on the first run, which was suspicious, so I deliberately
broke two guarantees to check the tests would notice.

The password-in-logs tests caught their breach. **The "decrypted content is never
persisted" test did not** — it scanned stored bytes for a known string, but PDF text lives
in compressed streams, so that string is absent even from a fully decrypted file. The test
could never have failed. It now asserts the stored object still *requires a password*,
and that version does fail under the same mutation.

A negative security test that cannot fail is worse than no test: it produces a false sense
of coverage exactly where it matters most.

### Phase 6 — Extraction and normalisation ✅
- **`DocumentExtractor` interface** with two implementations — pdfplumber (native text)
  and RapidOCR (scanned) — plus a router that prefers the native layer.
- **One geometric parser** (`services/normalization.py`) serving both sources: it finds
  column *labels* in the header band, derives boundaries from their positions, and
  assigns cells by where they sit.
- **Bank profiles as synonym sets**, not per-bank templates — which is the honest design
  given research found no public per-bank column specification.
- **`LLMProvider`** — Anthropic implementation plus a deterministic no-op. Used *only*
  for header fields the parser could not find, given the header block with long digit
  runs masked, never the ledger, never the account number, never a verdict.
- **Extraction service** persisting every attempt and moving documents to `EXTRACTED`.

**Verified:** `pytest` — **231 passed** (73 new); `ruff` clean; a live run through the
real server extracting 435/435 transactions from an 11-page statement in 1.1s, with the
account number masked at rest.

#### Extraction accuracy, measured against corpus ground truth

| Layout | Transactions | Balance breaks | Header fields |
|---|---|---|---|
| SBI, HDFC, ICICI, Axis, Kotak (native) | **100%** (392–449 each) | **0** | all exact |
| Clean scan, OCR | **437 / 437** | **0** | all exact |
| Degraded scan, OCR | 0 usable — aborts after 2 of 11 pages | — | confidence 0.0 → REVIEW |

Exact balance reconciliation on every valid document is the property ADR-007 rests on:
if extraction were even slightly lossy, the balance-continuity validator would report
breaks on perfectly good statements and the product's best completeness signal would be
worthless. The defect documents show precisely the signatures Phase 3 designed in — the
edited balance as a two-row discontinuity, the missing pages as one seam plus a page-
numbering gap.

#### Three bugs worth recording
1. **A `for/else` control-flow bug** aborted the header scan after the first
   non-matching start position, so *zero* transactions parsed on every layout while
   every header field extracted perfectly. The symptom looked like a table-detection
   problem; the cause was four lines of loop control.
2. **Overflowing narration landed in the numeric columns**, so joining a column's cells
   and parsing the result failed — silently losing amounts on 3 of 5 layouts (Kotak
   worst at 265/435). Amount reading now works right-to-left and takes the first thing
   that parses. A lost amount does not look like a bug downstream; it looks like a
   balance break, i.e. a defect attributed to the customer's document.
3. **OCR dropped the spaces around the period separator** — `01/03/26to31/08/26` from a
   perfectly legible scan. Period parsing now falls back to finding date-shaped tokens,
   because splitting on a bare hyphen is unsafe when the dates contain hyphens.

#### Measured facts that shaped the design (ADR-016)
- Local OCR costs **~20s per A4 page**; the native path does 11 pages in **1.1s**.
- **Page-level parallelism is counter-productive: 0.61x** (slower). ONNX Runtime already
  saturates the cores. Worth recording because it is the obvious optimisation.
- **Early abort on poor reads** turns a 4-minute job into 68 seconds. It is also correct
  product behaviour: the outcome was going to be REVIEW either way.

#### Two YAML traps
`yes` as a bank key parsed as boolean `true`, and double-quoted regexes had their
backslashes eaten. Both failed loudly at load because the config loader is strict —
which is the argument for validating config at startup rather than on first use.

#### Contract change
Default LLM model moved from `claude-sonnet-5` to **`claude-opus-5`**, per Anthropic's
current API guidance not to downgrade for cost without the user asking.

### Phase 7 — Validation engine ✅ *(the core product phase)*
- **17 rules** across readability, document type, period, completeness, integrity and
  identity (`app/validators/`), each returning a status plus the **evidence** it was
  built from.
- **Confidence composer** (`domain/confidence.py`) — weighted field score × rule
  coverage, reporting its components and the weakest fields, not just a number.
- **Resolution engine** (`services/validation_engine.py`) — the only code in the system
  that produces a verdict, and it consults no LLM.
- **Message composition** (`services/messages.py`) — every FIX quotes the actual values
  from the rule's evidence.
- **The reason-code catalogue moved into config**, so the tables in `PRODUCT_SPEC.md` §8
  are executable rather than descriptive: outcome, actionability and composite gating all
  live in `validation_rules.yaml` and are validated at startup.

**Verified:** `pytest` — **337 passed** (333 fast + 4 slow OCR); `ruff` clean; and a live
correction loop through the real server reproducing the spec's example messages verbatim.

#### The product contract, end to end

| Scenario | Document | Outcome | Reason |
|---|---|---|---|
| 1 Valid | 5 bank layouts | **PASS** | — |
| 2 Wrong period | 4-month statement | **FIX** → re-upload → **PASS** | `PERIOD_INSUFFICIENT_COVERAGE` |
| 2b Stale period | ends 6 months ago | **FIX** | `PERIOD_STALE` |
| 3 Password | AES-256 encrypted | prompt → **PASS** | — |
| 4 Wrong document | GST certificate | **FIX** | `DOC_TYPE_MISMATCH` |
| 5 Unusable | corrupt / not-a-PDF / empty | **FIX** ×3 | three *different* instructions |
| 6 Incomplete | pages removed | **FIX** | `COMPLETENESS_MISSING_PAGES` |
| 6b Edited balance | one figure altered | **REVIEW** | `COMPLETENESS_BALANCE_BREAK` |
| 7 Low confidence | degraded scan | **REVIEW** | `EXTRACTION_LOW_CONFIDENCE` |
| 7b Identity | individual's account | **REVIEW** | `IDENTITY_MISMATCH` |
| Control | clean scan | **PASS** | proves confidence tracks image quality |
| Control | consumer-editor metadata | **PASS** | one weak signal must not escalate |

**19 of 19 corpus documents reach their declared outcome and reason code.**

#### Two flaws the implementation exposed in the Phase 1 confidence model

1. **The composite score was vetoing structural verdicts (ADR-018).** It measures how
   well we read *a bank statement*; against a GST certificate it is near zero *because
   there was no statement to read*. Gating on it turned the most confident verdict
   available — "this is not a bank statement, please send one" — into a needless manual
   review. Reason codes now carry `composite_gated`, and structural findings are ungated.
2. **A downgraded FIX was reporting the wrong reason (ADR-019).** When low confidence
   forces a FIX to REVIEW, reporting the rule that fired implies we know the document is
   at fault. What we actually know is that we could not read it well enough to decide.
   The primary reason is now `EXTRACTION_LOW_CONFIDENCE` with `downgraded_from` recorded
   — which also stops every low-confidence case being filed under whichever rule happened
   to fire first, distorting the failure-reason distribution.

#### Design decisions inside the rules
- **Readability gates document type.** If we could not read it, we cannot claim it is the
  *wrong* document — that would send the customer to fetch a statement they may already
  have sent.
- **Completeness rules are evaluated together**, because their findings explain one
  another. A page-numbering gap plus a balance break at that seam is one problem seen
  twice; the balance rule downgrades itself to a SIGNAL so the customer gets one clear
  instruction instead of two competing ones.
- **A single integrity signal never escalates.** Two must agree, or one must co-occur
  with a balance break. Otherwise Operations drowns in noise and learns to dismiss the
  queue.
- **Identity failures are non-blocking and REVIEW-only.** A sole proprietor's current
  account is legitimately in an individual's name; automatically telling them they sent
  the wrong account would be wrong often enough to damage trust in every other message.

### Phase 8 — Outcome handling, Sales handoff and REVIEW resolution ✅
- **`messages` table + `MessagingProvider`** — one auditable record of everything the
  system said to anyone, on two channels (customer thread, Sales notifications).
  `SimulatedMessagingProvider` records and delivers nothing; WhatsApp plugs in here.
- **The conversation is now structural.** Every verdict emits a customer message from
  inside `record_outcome`, so a verdict cannot be recorded without the customer being
  told.
- **Sales notification on PASS** with the structured summary — enough to act without
  opening the document.
- **LOS handoff** (`GET /applications/{id}/handoff`, ops-gated) — the payload from
  `PRODUCT_SPEC.md` §21, including the per-check table and a signed download link.
- **Operations review queue** — list, card and decision endpoints, oldest-first.
- **5 new endpoints**; 16 in total.

**Verified:** `pytest` — **366 passed**; `ruff` clean; and a live run through the real
server: identity mismatch → REVIEW → Operations card with evidence → operator accepts →
customer told → Sales notified → LOS handoff rendered.

#### Design decisions
- **Accepting a review still counts as first-time clearance (ADR-021).** The customer
  sent a usable document first time; a human needing to look at it is *our* uncertainty,
  not their rework. Counting it against them would make the headline metric measure our
  confidence rather than their friction — and it would improve automatically whenever we
  made the engine more aggressive. Manual-review rate is tracked separately, which is
  where the cost of our uncertainty belongs.
- **Customer messages stay deterministic (ADR-020).** This is a *narrower* use of the LLM
  than the brief suggested. These messages must quote exact values, be reproducible, and
  never hedge into "there was a problem with your document" — and rendering them from the
  firing rule's evidence also guarantees the customer message and the Operations card
  describe the same finding.
- **The review queue is oldest-first.** Worked newest-first, a queue quietly strands the
  cases that have waited longest — which is exactly the pendency the product exists to cut.
- **Message bodies are not written to the audit log.** The event log has the longest
  retention of anything in the system, and the body is already stored and auditable.

#### Two things mutation testing caught
All 30 new tests passed first run, so two guarantees were deliberately broken to check
the tests would notice.

1. **Leaking the message body into the audit payload** was caught. Good.
2. **Removing the "already resolved" guard on review resolution was *not*.** The test
   still passed, because a second guard — the document is no longer `IN_REVIEW` — catches
   the ordinary case. The protection was real, but the test was not exercising the guard
   it named. It now asserts each layer separately by constructing the state only the
   first guard can see.

#### Found while demonstrating
The LOS handoff showed `Identity FAIL` on a **VERIFIED** document — truthful, since a
human overrode the rule, but it reads as a contradiction to anyone downstream. The
handoff now carries a `manual_review` block naming the action, the reviewer and the note,
so Credit can see that a person made the call and who it was.

### Phase 9 — Customer experience ✅
- **`CustomerChat`** — a WhatsApp-style thread rendered from the server-side
  conversation, with the document bubble, processing states, the validation checklist and
  the requirement strip.
- **`Composer`** — two modes and no free-text box (ADR-022): attach a document, or supply
  a password when asked.
- **`SampleDocuments`** — the corpus as one-click scenarios, each labelled with the
  outcome it should produce. Submissions run through the real pipeline (ADR-023).
- **Backend:** `GET /demo/samples` and `POST /applications/{id}/documents/sample`, gated
  on `demo_mode`. 17 endpoints in total.

**Verified:** `pytest` — **373 passed**; `ruff` clean; TypeScript build clean; and the
scenarios driven **through the actual UI in a browser** — valid → PASS, wrong period →
FIX → re-upload → PASS, encrypted → prompt → wrong password → correct password → PASS,
balance break → REVIEW.

#### Three bugs that only appear when you click
Every one of these passed the backend suite and would have survived a code review.

1. **Password polling froze.** After sending a password the UI stopped polling the moment
   it saw `AWAITING_PASSWORD` again — which is the state the document is *still in* until
   the worker picks the attempt up. The customer was left on a stale attempt count and
   would never have seen the result. Polling now continues until the attempt count
   changes or the document leaves that state.
2. **A page reload lost the pending document.** The thread still showed "please enter the
   PDF password" while the composer offered a file picker: a conversation that asked for
   something and then provided no way to answer. The client now recovers the live
   submission from the server, because the conversation is server state.
3. **The spinner never stopped on REVIEW**, and the requirement strip sat on a stale
   `PENDING`. From the customer's side the system had finished and was waiting on a human,
   but the UI said it was still thinking — reproducing the opaque wait this product
   exists to remove.

#### Design decisions
- **No free-text input (ADR-022).** An empty message box is a promise: it invites an
  entire conversation the product cannot hold. Every customer action maps to a state
  transition the backend already models.
- **The UI styles, it never composes.** Message bodies render exactly as the backend
  wrote them; lines starting with ✓/❌ are lifted into checklist rows, which is
  presentation only. The frontend cannot change what a customer is told.
- **Polling, not sockets** (`MVP_Product_Requirements_and_Build_Plan.md` §22) — with a
  5-second backoff once a document is with Operations, since nothing is moving on its own.

#### Verified in the browser
The customer UI contains **no digit run of nine or more characters anywhere** — no
account numbers, masked or otherwise, because a customer-facing message has no reason to
quote one.

### Phase 10 — Operations and Sales ✅
- **Operations** — the review queue (oldest-first) with a card showing the confidence
  *components*, every rule that ran with its evidence, the masked extracted summary, a
  signed document link, and both decisions.
- **Sales / LOS** — the handoff view from `PRODUCT_SPEC.md` §21: application list with
  requirement status, the verified document, the per-check validation table, the
  first-time-clearance metric, and a signed download.
- **`OpsGate`** — the operator credential typed rather than bundled (ADR-025), with the
  prototype limitation stated on the screen where an operator will read it.
- **Backend:** ops-gated `GET /applications`. **17 endpoints**, three tabs — borrower,
  operator, lender.

**Verified:** `pytest` — **379 passed**; `ruff` clean; TypeScript build clean; and the
whole operator path driven **in the browser** — queue → card → accept → customer told →
Sales notified → LOS handoff rendering the manual-override line.

#### The bug worth the whole phase (ADR-024)
Clicking through the queue produced a 422: the document had been **superseded** by a
later submission while it sat in Operations, leaving a review that could never be
resolved.

The error was the mild part. The quiet harm was that the queue looked busier than the
work actually was, and because it is worked **oldest-first** — deliberately, so the
longest-waiting customer is served first — the dead cards floated to the top. The
ordering that exists to protect customers would have served un-actionable work first.
Superseding now withdraws open reviews, audited, with no reviewer action recorded:
nobody decided anything.

#### Two more found by clicking
- **`[object Object]` in the error banner.** FastAPI returns `detail` as a *string* for a
  raised `HTTPException` but an **array of objects** for a validation failure. Treating
  both as strings hid a real backend error behind a meaningless banner for several
  minutes — which is exactly the failure mode this product exists to remove, reproduced
  in our own UI.
- **The API client silently dropped `Content-Type`.** `...init` was spread *after*
  `headers`, so any request that also set a header (every operations call) overwrote the
  merged headers and sent JSON with no content type. FastAPI then rejected the body with
  a validation error that looked like a schema problem rather than a transport one.

#### A usability fix worth naming
The balance-break evidence first rendered as raw JSON: `expected_paise 428321021`. An
operator reading that has to do mental arithmetic before they can judge anything, which
defeats the point of showing evidence at all. It is now a table in rupees — and the
mirror-image ±₹1,47,500.00 across two consecutive rows is the signature of a single
edited figure, legible at a glance.

---

### Phase 10.5 — What a real document did to it ✅

Phase 11 (analytics) was dropped at the user's request. Before anything else, the build
was tested with a **real bank statement** — nine phone screenshots of a Canara Bank
mobile app, exported as a PDF — and it failed in three distinct ways at once. This
section is the repair, and it is the most useful thing in this file: every earlier phase
was validated against a corpus we generated ourselves, which means it was validated
against our own assumptions.

**What the tester saw.** Two minutes of no visible progress, then:
*"This one needs a quick manual check by our team."* And, from a sample they had clicked
while waiting, a verdict naming the wrong account type and unfamiliar dates — which they
reasonably read as a judgement on their own statement.

**What was actually wrong.** Four independent defects, each of which alone would have
been survivable.

1. **The parser discarded ~90% of the statement (ADR-029).** The header read
   `DATE | Dr/Cr | AMOUNT | BALANCE`. `Dr/Cr` matched no synonym, so it was skipped — and
   the column boundaries, drawn between the labels we *did* recognise, put its region
   inside the date column. Every date read as `24/04/2025 Dr`, failed to parse, and the
   row was dropped as a continuation line. 6 transactions survived out of roughly 70.
2. **The engine then reported that emptiness as the customer's fault (ADR-026).** With
   six rows and a composite confidence of 0.18, it returned the *specific* finding
   `COMPLETENESS_BALANCE_BREAK` — asserting that a balance chain it had never read did
   not reconcile. The confidence gate existed but was only checked on the path to FIX;
   REVIEW-default codes bypassed it.
3. **The bank was named as "HDFC Bank" (ADR-031).** Canara was not in the bank table, and
   the matcher searched the whole page — which, on a statement full of UPI narrations,
   means it found whoever the customer had last paid.
4. **Nothing said their document had been dropped (ADR-030).** Clicking a sample while
   the first was in flight superseded it, correctly and silently, and the sample's
   verdict appeared in the same thread with nothing to distinguish the two.

**The fixes, and what they measured.**

| | Before | After |
|---|---|---|
| Transactions parsed | 6 (from 9 pages) | 23 (from 3 pages) |
| Extraction time | 91.6s | 30.5s |
| Bank identified | HDFC Bank ✗ | Canara Bank ✓ |
| Verdict | `REVIEW / COMPLETENESS_BALANCE_BREAK` | `FIX / READABILITY_PARTIAL_CAPTURE` |
| What the customer is told | "needs a manual check" | "the amount column is cut off — send the PDF your bank issues" |

- **ADR-029** — unmatched header cells now claim their own column instead of leaving a
  gap for a neighbour to absorb; `_date_from` tolerates a stray token, as `_amount_from`
  already did.
- **ADR-026** — the confidence gate runs before the REVIEW branch, and R-CMP-002 needs
  10 complete transactions before it may declare a break.
- **R-CMP-005 / `READABILITY_PARTIAL_CAPTURE`** — a new rule for the banking-app
  screenshot: legible, genuine, and missing a column. FIX, not REVIEW, because the
  customer can fix it in one message.
- **ADR-028** — OCR now stops when the *structure* is futile, not only when the reading
  is poor. It runs the production parser over the pages read so far and stops if a table
  was found, rows were parsed, and none is complete.
- **ADR-027** — readability is judged over the pages read, so stopping early no longer
  reads as "your scan is bad".
- **ADR-031** — bank identification bounded by the transaction-table header row; IFSC
  search bounded identically (it is tried first, so an unbounded one would outrank the
  name silently); twelve public-sector and mid-size private banks added.
- **ADR-030** — supersession now tells the customer, naming both files; every message
  carries its document's filename, stamped centrally in `record_message` so none can be
  written without it; the chat shows attribution once a thread holds more than one
  document. The processing indicator now explains a long OCR wait instead of repeating
  the same word for ninety seconds.

**Verified:** `pytest` — **388 passed**; `ruff` clean; TypeScript build clean; and the
actual failing document re-run end to end, from `REVIEW / COMPLETENESS_BALANCE_BREAK` to
`FIX / READABILITY_PARTIAL_CAPTURE` with an actionable message.

#### The lesson worth keeping
Nineteen synthetic documents passed. One real one broke four things, and the four were
not independent: a parsing defect (1) became a validation defect (2) because the engine
had no way to distinguish "this document is wrong" from "we read it badly". That
distinction is the product. A corpus you generate cannot test it, because you do not
generate documents you cannot parse.

---

## Currently working on

Nothing — the repair above is complete and verified. Phase 11 (analytics) was dropped at
the user's request. Phase 12 (test hardening and documentation) and Phase 13 (optional
Cloud Run deployment) remain.

---

## Next

| Phase | Summary |
|---|---|
| **11** | Analytics and metrics |
| 12 | Test hardening and documentation |
| 13 | *(Optional)* Cloud Run deployment |

---

## Blockers

None. One environment constraint worth recording: **Docker is not installed on this
machine**, which is why SQLite became the default database (ADR-012) and why
`docker-compose.yml` is untested here.

---

## Decisions taken

See `docs/DECISIONS.md` for full reasoning.

| ADR | Decision |
|---|---|
| 001 | Deterministic rules adjudicate; LLM only assists |
| 002 | Local-first pluggable extraction (pdfplumber + OCR), not Document AI — **now regulatory** |
| 003 | DB-backed job queue, not Pub/Sub |
| 004 | Modular monolith |
| 005 | Cloud Run optional and late *(Docker-primary part superseded by ADR-012)* |
| 006 | `document_requirements` as a first-class entity |
| 007 | Balance continuity is the primary completeness check |
| 008 | Integrity signals route to REVIEW only, never a fraud verdict |
| 009 | Explainable composite confidence (field confidence + rule coverage) |
| 010 | Real LLM with deterministic fallback; tests pass without an API key |
| 011 | Prototype auth: shared secret + short-lived signed URLs |
| 012 | SQLite by default, Postgres for parity — Docker is not installed here; supersedes part of ADR-005 |
| 013 | Validation rules stay in version-controlled YAML; no `validation_rules` table |
| 014 | Failed processing routes to REVIEW, never to silence |
| 015 | Process-local, single-use password vault; never persisted, never hashed |
| 016 | RapidOCR for the scanned path; ~20s/page measured, early abort on poor reads |
| 017 | One geometric parser for both text sources; bank profiles are synonym sets |
| 018 | Confidence must not veto structural verdicts (`composite_gated`) |
| 019 | A downgraded FIX reports low confidence, not the rule that fired |
| 020 | Customer messages are deterministic templates, not LLM prose |
| 021 | Accepting a review still counts as first-time clearance |
| 022 | The customer composer has no free-text input |
| 023 | Demo sample submission goes through the real pipeline |
| 024 | A superseded document withdraws its open review |
| 025 | The operations credential is typed, never bundled |
| 026 | A specific finding requires the confidence to make it |
| 027 | Readability is judged over the pages read, not the pages in the document |
| 028 | OCR stops when the structure is futile, not only when the reading is poor |
| 029 | An unrecognised column is a column, not a gap |
| 030 | Silence about supersession is indistinguishable from lying |
| 031 | A bank is identified from the masthead, never from the body |

---

## Open questions / assumptions to revisit

1. **R-PER-002 recency tolerance (35 days).** ASSUMPTION — no public evidence. First thing
   to calibrate against a real lender's policy. Config-driven.
2. **Confidence weights and thresholds** (`pass_threshold` 0.80, `fix_threshold` 0.65).
   Reasoned, not fitted. To be tuned against the corpus in Phase 7.
3. **Account-type detection (R-DOC-002).** Not all statements print the account type.
   Currently degrades to `NOT_EVALUATED` rather than failing. Revisit against real samples.
4. **WhatsApp Business API media handling** vs. the 2025 localisation requirement is
   genuinely unresolved — flagged as an open risk in the product thesis, not glossed over.
5. **Anthropic API key** not yet supplied. Deterministic fallback runs until it is.
6. **The password vault is process-local (ADR-015).** It works only while the worker
   runs in-process. Out-of-process workers would require re-prompting the customer.
7. **Corpus bank profiles are UNVERIFIED** against real statements. They provide
   structural diversity, not fidelity; no blocking rule depends on a bank-specific
   detail. Worth validating against real anonymised samples if any become available.
8. **Image submissions** (photos of statements) deliberately excluded from the MVP. This is
   a real customer behaviour and the most likely first scope increment.
