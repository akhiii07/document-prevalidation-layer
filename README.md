# MSME Document Collection & Pre-Validation Layer

Real-time bank-statement validation for MSME lending. A borrower sends a document; the
system validates it within seconds and returns **PASS**, **FIX**, or **REVIEW** — each with
a specific reason and a specific next action.

The problem it targets is **document-related rework**: today a document enters downstream
processing and only later does someone discover it is the wrong period, unreadable,
password-protected, incomplete, or the wrong document entirely. This product moves that
discovery to the point of collection.

> **Status:** Phases 0–10 complete, plus a repair pass driven by a real submission —
> **all three views run end to end in a browser**: the borrower's chat, the Operations
> review queue, and the Sales / LOS handoff. **392 tests pass.**
> See [PROGRESS.md](PROGRESS.md).
> **Synthetic documents only.** No real customer financial data, at any stage.

The most instructive part of this repository is
[Phase 10.5 in PROGRESS.md](PROGRESS.md): nineteen synthetic documents passed, then one
real bank statement — nine phone screenshots of a banking app — broke four things at once
and produced a verdict that was specific, confident, and wrong. A corpus you generate
yourself structurally cannot find that class of bug, because you do not generate documents
you cannot parse.

---

## Try it

**[akhiii07.github.io/document-prevalidation-layer](https://akhiii07.github.io/document-prevalidation-layer/)**

A **recorded** demo. GitHub Pages serves files, not processes, so there is no pipeline
behind that page: `tools/capture_demo_fixtures.py` drives the real system, records what
it returned and when, and the static build replays those recordings against the clock.
Every message, reason code, rule result, confidence figure and duration is therefore
something the product genuinely produced — what is simulated is only *which* recording
plays and when, never its content. The one thing it cannot do is validate a document
nobody has validated yet, so uploads are refused with an explanation rather than faked.

All three views are live there: the borrower's chat, the Operations review queue and the
Sales / LOS handoff. To validate your own document, run it locally (below).

---

## Documentation

Read in this order:

| Document | What it covers |
|---|---|
| [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) | Scope, users, the PASS/FIX/REVIEW contract, reason codes, acceptance scenarios |
| [docs/VALIDATION_RULES.md](docs/VALIDATION_RULES.md) | All 18 rules, each evidence-tagged; the confidence model |
| [docs/RESEARCH_BANK_FORMATS.md](docs/RESEARCH_BANK_FORMATS.md) | Indian bank statement structure, password conventions, integrity signals |
| [docs/RESEARCH_REGULATORY.md](docs/RESEARCH_REGULATORY.md) | RBI Digital Lending Directions 2025, DPDP Act, data-handling constraints |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADR-001…031 — decision / reason / alternative / tradeoff |
| [corpus/README.md](corpus/README.md) | The synthetic test corpus, manifest schema, and what each document proves |
| [PROGRESS.md](PROGRESS.md) | Phase status, open questions, assumptions to revisit |

---

## Running it

Requires **Python ≥ 3.11** and **Node ≥ 20**. Docker is optional.

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # macOS/Linux: .venv/bin/python
cp .env.example .env
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

API docs at http://127.0.0.1:8000/docs · health at `/health` · readiness at `/ready`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens http://localhost:5173. The dev server proxies `/api` to the backend, so the frontend
never holds a backend hostname or a secret.

Three tabs, one per person in the journey:

| Tab | Who | What it shows |
|---|---|---|
| **Customer** | Borrower | The simulated WhatsApp thread. Expand **Synthetic test documents** to send any corpus scenario with one click — each runs through the real pipeline (ADR-023). |
| **Operations** | Reviewer | The REVIEW queue, with the confidence breakdown and every rule's evidence. |
| **Sales / LOS** | Lender | What the product hands downstream: verified document, structured data, per-check table. |

The Operations and Sales tabs ask for the operations secret — `dev-ops-secret-change-me`
by default. It is typed, never bundled (ADR-025).

### Synthetic corpus

```bash
backend/.venv/Scripts/python.exe -m corpus.generator.build
```

Generates 19 synthetic bank statements covering every acceptance scenario, plus a
manifest recording the defect injected into each and the outcome it should produce. The
test suite builds it automatically if missing. See [corpus/README.md](corpus/README.md).

### Tests

```bash
cd backend
.venv/Scripts/python.exe -m pip install -e ".[dev,corpus]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check . ../corpus
```

Tests pass with **no** Anthropic API key present — that is asserted, not incidental
(ADR-010).

OCR-backed tests are excluded by default because they cost ~20s per page (ADR-016).
Run them explicitly:

```bash
.venv/Scripts/python.exe -m pytest -m slow
```

---

## Architecture at a glance

```
Simulated WhatsApp UI ──┐
                        ├─→ FastAPI (modular monolith) ─→ Storage · Postgres/SQLite · JobQueue
Operations dashboard ───┘                                              │
                                                                       ▼
                                                    Worker: file checks → extraction →
                                                    normalisation → validation engine
                                                                       │
                                                    ┌──────────────────┼──────────────────┐
                                                  PASS                FIX               REVIEW
                                                    │                  │                  │
                                            Sales / mock LOS   correction loop      Ops queue
```

**Modular monolith** (ADR-004). Every external dependency sits behind an interface —
`DocumentExtractor`, `StorageProvider`, `JobQueue`, `LLMProvider`, `MessagingProvider` — so
the local implementation used in development can be swapped for a managed service without
touching callers.

### Principles worth knowing before reading the code

1. **Deterministic rules decide; the LLM only assists.** The LLM normalises unfamiliar
   layouts and phrases messages. It cannot set or change a verdict (ADR-001).
2. **Uncertainty resolves to REVIEW, never to PASS.** There is no code path from a
   low-confidence extraction to an accepted document.
3. **Rule thresholds are configuration**, in
   [`validation_rules.yaml`](backend/app/config/validation_rules.yaml), never hardcoded.
4. **Integrity signals raise a human check, never a fraud verdict** (ADR-008).
5. **Passwords are never logged, persisted, or hashed**; decrypted content is never written
   to disk (`RESEARCH_REGULATORY.md` §3).
6. **`document.status` is assigned in exactly one place** —
   `services/document_state.transition()`. That is what makes "an audit event for every
   transition" a guarantee rather than a habit.
7. **Processing failure routes to REVIEW, never to silence** (ADR-014). A customer who
   submits a document and hears nothing is the failure this product exists to remove.

### Database

```bash
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
```

Eight tables. `document_requirements` is the obligation; `documents` are submissions
against it — that separation is what makes First-Time Document Clearance Rate computable
(ADR-006). `document_events` deliberately has **no foreign keys**: content is purgeable
under a retention policy, the audit trail is not.

---

## Database

**SQLite by default** — the stack runs with zero external dependencies (ADR-012).

For PostgreSQL parity:

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://docverify:docverify@localhost:5432/docverify
```

Models use only portable column types (`app/db/base.py`), so the two backends stay honest.

---

## Project layout

```
backend/app/
├── api/          HTTP surface
├── services/     orchestration (ingestion, extraction, validation, correction, review)
├── validators/   one module per rule family
├── providers/    extractor / storage / queue / llm / messaging interfaces + impls
├── domain/       state machine, outcome types, confidence composer
├── models/       SQLAlchemy models
├── config/       validation_rules.yaml + typed loader
└── workers/      document processor
frontend/src/     pages (CustomerChat, OperationsDashboard, SalesHandoff) + components
corpus/           synthetic document generator
docs/             product spec, validation rules, research, ADRs
```

---

## Scope boundaries

**Not built, deliberately:** credit scoring, eligibility, approval, underwriting,
disbursement, fraud decisioning, LOS replacement, cross-document intelligence, production
WhatsApp infrastructure.

**Simulated in the MVP:** the WhatsApp channel, Sales notification, and the downstream LOS.
**Real in the MVP:** PDF processing, extraction, the validation engine, the correction loop,
state transitions, the review workflow, and metrics.
