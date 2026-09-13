"""The seven acceptance scenarios, end to end through the HTTP API.

`PRODUCT_SPEC.md` §10 defines what the MVP must demonstrate. These tests drive the real
upload endpoint, the real queue, the real worker and the real validation engine against
real synthetic PDFs — nothing is stubbed between the file arriving and the verdict.

Scenario 2 is the one that matters most: it is the correction loop, and it proves the
metric is computed against the *requirement* rather than the documents submitted for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus, EventType, JobType, RequirementStatus
from app.models.application import DocumentRequirement
from app.models.event import DocumentEvent
from app.models.review import Review
from app.services.pipeline import process_document
from app.workers.document_processor import Worker


def drain(limit: int = 20) -> int:
    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: process_document})
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1
    return processed


@pytest.fixture
def app_id(client: TestClient, session: Session, by_id: dict) -> str:
    """An application whose business name matches the corpus statements."""
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-31007",
            "borrower_name": "Rajesh Kumar Sharma",
            "business_name": by_id["valid_hdfc"]["application"]["business_name"],
            "phone_number": "+919820000000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def submit(client: TestClient, app_id: str, path: Path) -> str:
    with path.open("rb") as fh:
        response = client.post(
            f"/applications/{app_id}/documents",
            files={"file": (path.name, fh, "application/pdf")},
        )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    drain()
    return document_id


def status_of(client: TestClient, document_id: str) -> dict:
    return client.get(f"/documents/{document_id}/status").json()


def requirement_of(session: Session, app_id: str) -> DocumentRequirement:
    session.expire_all()
    return session.scalars(
        select(DocumentRequirement).where(DocumentRequirement.application_id == app_id)
    ).one()


# ------------------------------------------------------------------ scenario 1


@pytest.mark.scenario
@pytest.mark.parametrize("bank", ["sbi", "hdfc", "icici", "axis", "kotak"])
def test_scenario_1_valid_statement_passes(
    bank: str, client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    document_id = submit(client, app_id, corpus_path(f"valid_{bank}"))
    result = status_of(client, document_id)

    assert result["outcome"] == "PASS"
    assert result["status"] == DocumentStatus.PASSED
    assert "Bank statement verified" in result["customer_message"]

    requirement = requirement_of(session, app_id)
    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is True


# ------------------------------------------------------------------ scenario 2


@pytest.mark.scenario
def test_scenario_2_wrong_period_then_reupload_clears_the_requirement(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """FIX → customer re-uploads → PASS.

    The heart of the product. Two things are asserted beyond the outcomes: the customer
    is told *specifically* what to do, and the requirement is recorded as cleared but
    **not first time** — with the denominator still one requirement, not two documents.
    A metric that counted documents would improve as rework increased.
    """
    first = submit(client, app_id, corpus_path("wrong_period_short_hdfc"))
    fix = status_of(client, first)

    assert fix["outcome"] == "FIX"
    assert fix["reason_code"] == "PERIOD_INSUFFICIENT_COVERAGE"
    assert "6 months" in fix["customer_message"]
    assert "Please upload" in fix["customer_message"]

    requirement = requirement_of(session, app_id)
    assert requirement.status == RequirementStatus.AWAITING_CUSTOMER_ACTION

    second = submit(client, app_id, corpus_path("valid_hdfc"))
    assert status_of(client, second)["outcome"] == "PASS"

    requirement = requirement_of(session, app_id)
    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is False, "a corrected document is not first-time"
    assert requirement.submission_count == 2
    assert requirement.cleared_by_document_id == second


@pytest.mark.scenario
def test_scenario_2b_stale_period_asks_for_a_recent_statement(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """Coverage and recency fail for different reasons and need different instructions."""
    document_id = submit(client, app_id, corpus_path("wrong_period_stale_sbi"))
    result = status_of(client, document_id)

    assert result["reason_code"] == "PERIOD_STALE"
    assert "most recent statement" in result["customer_message"]


# ------------------------------------------------------------------ scenario 3


@pytest.mark.scenario
def test_scenario_3_password_protected_statement_passes_after_unlock(
    client: TestClient, session: Session, app_id: str, corpus_path, by_id: dict
) -> None:
    document_id = submit(client, app_id, corpus_path("password_protected_icici"))
    assert status_of(client, document_id)["awaiting_password"] is True

    client.post(
        f"/documents/{document_id}/password",
        json={"password": by_id["password_protected_icici"]["password"]},
    )
    drain()

    result = status_of(client, document_id)
    assert result["outcome"] == "PASS"
    assert requirement_of(session, app_id).first_time_cleared is True, (
        "supplying a password is not a correction -- nothing was wrong with the document"
    )


# ------------------------------------------------------------------ scenario 4


@pytest.mark.scenario
def test_scenario_4_wrong_document_asks_for_a_bank_statement(
    client: TestClient, app_id: str, corpus_path
) -> None:
    document_id = submit(client, app_id, corpus_path("wrong_document_gst_certificate"))
    result = status_of(client, document_id)

    assert result["outcome"] == "FIX"
    assert result["reason_code"] == "DOC_TYPE_MISMATCH"
    assert "bank statement" in result["customer_message"].lower()


# ------------------------------------------------------------------ scenario 5


@pytest.mark.scenario
@pytest.mark.parametrize(
    ("doc_id", "reason"),
    [
        ("corrupt_truncated_axis", "FILE_CORRUPT"),
        ("invalid_not_a_pdf", "FILE_UNSUPPORTED_TYPE"),
        ("invalid_empty", "FILE_EMPTY"),
    ],
)
def test_scenario_5_unusable_files_get_specific_advice(
    doc_id: str, reason: str, client: TestClient, app_id: str, corpus_path
) -> None:
    """Three different unusable files, three different instructions. Collapsing them
    into "there was a problem with your file" is how document products give advice
    nobody can act on."""
    document_id = submit(client, app_id, corpus_path(doc_id))
    result = status_of(client, document_id)

    assert result["outcome"] == "FIX"
    assert result["reason_code"] == reason
    assert result["customer_message"]


# ------------------------------------------------------------------ scenario 6


@pytest.mark.scenario
def test_scenario_6_missing_pages_names_the_missing_pages(
    client: TestClient, app_id: str, corpus_path
) -> None:
    document_id = submit(client, app_id, corpus_path("incomplete_missing_pages_axis"))
    result = status_of(client, document_id)

    assert result["outcome"] == "FIX"
    assert result["reason_code"] == "COMPLETENESS_MISSING_PAGES"
    assert "complete statement" in result["customer_message"]


@pytest.mark.scenario
def test_scenario_6b_edited_balance_goes_to_a_human_not_the_customer(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """Every page present, one figure altered. Arithmetic cannot tell an edit from
    missing content, so this must not become a re-upload instruction (ADR-008)."""
    document_id = submit(client, app_id, corpus_path("balance_break_icici"))
    result = status_of(client, document_id)

    assert result["outcome"] == "REVIEW"
    assert result["reason_code"] == "COMPLETENESS_BALANCE_BREAK"

    session.expire_all()
    review = session.scalars(select(Review).where(Review.document_id == document_id)).one()
    assert review.reason_code == "COMPLETENESS_BALANCE_BREAK"

    # The customer is never told the document looks altered.
    message = result["customer_message"].lower()
    for word in ("tamper", "altered", "edited", "fraud", "suspicious"):
        assert word not in message


# ------------------------------------------------------------------ scenario 7


@pytest.mark.scenario
def test_scenario_7_identity_mismatch_opens_a_review(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """The system declines to decide rather than accusing a sole proprietor of sending
    the wrong account."""
    document_id = submit(client, app_id, corpus_path("identity_mismatch_axis"))
    result = status_of(client, document_id)

    assert result["outcome"] == "REVIEW"
    assert result["reason_code"] == "IDENTITY_MISMATCH"

    session.expire_all()
    assert session.scalars(select(Review).where(Review.document_id == document_id)).one()
    assert requirement_of(session, app_id).status == RequirementStatus.AWAITING_REVIEW


@pytest.mark.scenario
@pytest.mark.slow
def test_scenario_7b_degraded_scan_goes_to_review_not_a_guess(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """Scenario 7 proper: a document we cannot read confidently must reach a human
    rather than a confidently wrong verdict."""
    document_id = submit(client, app_id, corpus_path("scanned_poor_kotak"))
    result = status_of(client, document_id)

    assert result["outcome"] == "REVIEW"
    assert result["reason_code"] == "EXTRACTION_LOW_CONFIDENCE"


@pytest.mark.slow
def test_a_clean_scan_still_passes(client: TestClient, app_id: str, corpus_path) -> None:
    """The positive control: low confidence must track image *quality*, not merely the
    absence of a text layer."""
    document_id = submit(client, app_id, corpus_path("scanned_clean_hdfc"))
    assert status_of(client, document_id)["outcome"] == "PASS"


# ------------------------------------------------------------------ controls


@pytest.mark.scenario
def test_one_weak_integrity_signal_does_not_disturb_a_good_document(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """The negative control. A statement re-saved by a consumer PDF editor fires exactly
    one signal, below the escalation threshold, and must still PASS — otherwise
    Operations drowns in noise and learns to dismiss the queue."""
    document_id = submit(client, app_id, corpus_path("integrity_edited_producer_sbi"))
    assert status_of(client, document_id)["outcome"] == "PASS"


@pytest.mark.scenario
def test_savings_account_is_rejected_with_a_specific_instruction(
    client: TestClient, app_id: str, corpus_path
) -> None:
    document_id = submit(client, app_id, corpus_path("wrong_account_type_savings_hdfc"))
    result = status_of(client, document_id)

    assert result["reason_code"] == "ACCOUNT_TYPE_NOT_CURRENT"
    assert "current account" in result["customer_message"].lower()


# ------------------------------------------------------------------ audit


def test_a_full_pass_is_end_to_end_audited(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    document_id = submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    events = [
        e.event_type
        for e in session.scalars(
            select(DocumentEvent)
            .where(DocumentEvent.document_id == document_id)
            .order_by(DocumentEvent.created_at, DocumentEvent.id)
        ).all()
    ]
    for expected in (
        EventType.DOCUMENT_RECEIVED,
        EventType.EXTRACTION_COMPLETED,
        EventType.VALIDATION_COMPLETED,
        EventType.REQUIREMENT_CLEARED,
    ):
        assert expected in events, f"missing {expected} in {events}"


def test_the_verdict_records_every_rule_that_ran(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """Explainability is a requirement, not a nicety: a verdict nobody can reconstruct
    is not usable in a lending workflow."""
    from app.models.validation import ValidationResult

    document_id = submit(client, app_id, corpus_path("wrong_period_short_hdfc"))

    session.expire_all()
    result = session.scalars(
        select(ValidationResult).where(ValidationResult.document_id == document_id)
    ).one()

    assert result.rule_results
    rule_ids = {r["rule_id"] for r in result.rule_results}
    assert {"R-RED-001", "R-DOC-001", "R-PER-001", "R-PER-002", "R-CMP-002"} <= rule_ids

    period_rule = next(r for r in result.rule_results if r["rule_id"] == "R-PER-001")
    assert period_rule["status"] == "FAIL"
    assert period_rule["evidence"]["shortfall_days"] > 0

    assert result.composite_confidence is not None
    assert result.field_score is not None
    assert result.rule_coverage is not None
    assert result.rules_version is not None
