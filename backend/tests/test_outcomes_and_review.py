"""The downstream side: the conversation, the Sales handoff, and REVIEW resolution.

Phase 7 proved the system reaches the right verdict. These tests prove the verdict
actually *goes somewhere* — that the customer is told, Sales is notified, and a human can
resolve what the system declined to decide.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.domain.enums import (
    DocumentStatus,
    EventType,
    JobType,
    MessageChannel,
    MessageDirection,
    MessageKind,
    RequirementStatus,
    ReviewStatus,
)
from app.models.application import DocumentRequirement
from app.models.document import Document
from app.models.event import DocumentEvent
from app.models.message import Message
from app.models.review import Review
from app.providers.messaging import SimulatedMessagingProvider, _mask_recipient
from app.services.pipeline import process_document
from app.workers.document_processor import Worker

OPS = {"X-Ops-Secret": get_settings().ops_shared_secret.get_secret_value()}


def drain(limit: int = 20) -> int:
    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: process_document})
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1
    return processed


@pytest.fixture
def app_id(client: TestClient, session: Session, by_id: dict) -> str:
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-44021",
            "borrower_name": "Rajesh Kumar Sharma",
            "business_name": by_id["valid_hdfc"]["application"]["business_name"],
            "phone_number": "+919820001234",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def submit(client: TestClient, app_id: str, path: Path) -> str:
    with path.open("rb") as fh:
        response = client.post(
            f"/applications/{app_id}/documents",
            files={"file": (path.name, fh, "application/pdf")},
        )
    document_id = response.json()["id"]
    drain()
    return document_id


def customer_thread(client: TestClient, app_id: str) -> list[dict]:
    return client.get(f"/applications/{app_id}/messages").json()


def sales_thread(client: TestClient, app_id: str) -> list[dict]:
    return client.get(f"/applications/{app_id}/messages?channel=SALES").json()


# ------------------------------------------------------------------ conversation


def test_the_conversation_starts_when_sales_asks(client: TestClient, app_id: str) -> None:
    """The product's entry point: Sales tells the customer to send the statement here."""
    thread = customer_thread(client, app_id)

    assert len(thread) == 1
    assert thread[0]["kind"] == MessageKind.INSTRUCTION
    assert "bank statement" in thread[0]["body"].lower()
    assert "PDF" in thread[0]["body"]


def test_the_thread_reads_as_a_conversation(client: TestClient, app_id: str, corpus_path) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))
    thread = customer_thread(client, app_id)

    kinds = [(m["direction"], m["kind"]) for m in thread]
    assert kinds == [
        (MessageDirection.OUTBOUND, MessageKind.INSTRUCTION),
        (MessageDirection.INBOUND, MessageKind.DOCUMENT_RECEIVED),
        (MessageDirection.OUTBOUND, MessageKind.VALIDATION_RESULT),
    ]
    assert "Bank statement verified" in thread[-1]["body"]


def test_the_correction_loop_is_visible_in_the_thread(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """The whole product story in one transcript: asked, sent, told what was wrong,
    sent again, verified."""
    submit(client, app_id, corpus_path("wrong_period_short_hdfc"))
    submit(client, app_id, corpus_path("valid_hdfc"))

    thread = customer_thread(client, app_id)
    bodies = [m["body"] for m in thread]

    assert len(thread) == 5
    assert "I found one issue" in bodies[2]
    assert "Bank statement verified" in bodies[4]


def test_a_password_prompt_appears_in_the_thread(
    client: TestClient, app_id: str, corpus_path, by_id: dict
) -> None:
    document_id = submit(client, app_id, corpus_path("password_protected_icici"))

    thread = customer_thread(client, app_id)
    assert thread[-1]["kind"] == MessageKind.PASSWORD_REQUEST
    assert "password protected" in thread[-1]["body"]

    client.post(
        f"/documents/{document_id}/password",
        json={"password": by_id["password_protected_icici"]["password"]},
    )
    drain()
    assert "Bank statement verified" in customer_thread(client, app_id)[-1]["body"]


def test_the_thread_never_contains_a_password_or_account_number(
    client: TestClient, app_id: str, corpus_path, by_id: dict
) -> None:
    secret = by_id["password_protected_icici"]["password"]
    document_id = submit(client, app_id, corpus_path("password_protected_icici"))
    client.post(f"/documents/{document_id}/password", json={"password": secret})
    drain()

    transcript = " ".join(m["body"] + str(m["context"]) for m in customer_thread(client, app_id))
    assert secret not in transcript
    assert "010405001729" not in transcript


def test_customer_and_sales_threads_are_separate(
    client: TestClient, app_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))

    assert all(m["kind"] != MessageKind.SALES_NOTIFICATION for m in customer_thread(client, app_id))
    assert len(sales_thread(client, app_id)) == 1


def test_unknown_channel_is_rejected(client: TestClient, app_id: str) -> None:
    assert client.get(f"/applications/{app_id}/messages?channel=EMAIL").status_code == 422


# ------------------------------------------------------------------ sales


def test_sales_is_notified_only_on_pass(client: TestClient, app_id: str, corpus_path) -> None:
    submit(client, app_id, corpus_path("wrong_period_short_hdfc"))
    assert sales_thread(client, app_id) == [], "a FIX is the customer's business, not Sales'"

    submit(client, app_id, corpus_path("valid_hdfc"))
    notifications = sales_thread(client, app_id)
    assert len(notifications) == 1
    assert "FLX-44021" in notifications[0]["body"]


def test_the_sales_notification_carries_the_structured_summary(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """Enough to act without opening the document (`PRODUCT_SPEC.md` §19)."""
    submit(client, app_id, corpus_path("valid_hdfc"))
    body = sales_thread(client, app_id)[0]["body"]

    assert "HDFC Bank" in body
    assert "2026-03-01 to 2026-08-31" in body
    assert "XXXXXXXXXX3926" in body
    assert "50200047183926" not in body, "masked, always"


# ------------------------------------------------------------------ application list


def test_application_list_requires_ops_credentials(client: TestClient) -> None:
    """It is a list of borrowers -- names, businesses and their document status. That is
    exactly the kind of enumeration that should not be open, even in a prototype."""
    assert client.get("/applications").status_code == 401


def test_application_list_summarises_document_status(
    client: TestClient, app_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("wrong_period_short_hdfc"))
    submit(client, app_id, corpus_path("valid_hdfc"))

    rows = client.get("/applications", headers=OPS).json()
    row = next(r for r in rows if r["id"] == app_id)

    assert row["requirement_status"] == RequirementStatus.CLEARED
    assert row["submission_count"] == 2
    assert row["first_time_cleared"] is False
    assert row["verified_documents"] == 1


def test_application_list_is_newest_first(client: TestClient, app_id: str) -> None:
    client.post(
        "/applications",
        json={
            "external_reference": "FLX-44099",
            "borrower_name": "Newer Borrower",
            "business_name": "Newer Business Pvt Ltd",
            "phone_number": "+919820001299",
        },
    )
    rows = client.get("/applications", headers=OPS).json()
    assert rows[0]["external_reference"] == "FLX-44099"


# ------------------------------------------------------------------ LOS handoff


def test_handoff_requires_ops_credentials(client: TestClient, app_id: str) -> None:
    assert client.get(f"/applications/{app_id}/handoff").status_code == 401


def test_handoff_describes_the_verified_document(
    client: TestClient, app_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))
    payload = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()

    assert payload["application_reference"] == "FLX-44021"
    assert payload["requirement_status"] == RequirementStatus.CLEARED
    assert payload["first_time_cleared"] is True
    assert len(payload["documents"]) == 1

    document = payload["documents"][0]
    assert document["status"] == "VERIFIED"
    assert document["bank_name"] == "HDFC Bank"
    assert document["account_number_masked"] == "XXXXXXXXXX3926"
    assert document["period_start"] == "2026-03-01"
    assert document["transaction_count"] > 100
    assert document["download_url"].startswith("/documents/")


def test_handoff_shows_the_per_check_table(client: TestClient, app_id: str, corpus_path) -> None:
    """The table in `PRODUCT_SPEC.md` §21: Document Type / Period / Completeness /
    Readability, each PASS."""
    submit(client, app_id, corpus_path("valid_hdfc"))
    checks = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()["documents"][0][
        "validation"
    ]["checks"]

    for label in ("Document Type", "Period", "Completeness", "Readability"):
        assert checks.get(label) == "PASS", f"{label} missing or not passing: {checks}"


def test_handoff_offers_nothing_before_a_document_is_verified(
    client: TestClient, app_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("wrong_period_short_hdfc"))
    payload = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()

    assert payload["documents"] == []
    assert payload["requirement_status"] == RequirementStatus.AWAITING_CUSTOMER_ACTION


def test_the_handoff_download_link_works(client: TestClient, app_id: str, corpus_path) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))
    url = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()["documents"][0][
        "download_url"
    ]

    downloaded = client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")


# ------------------------------------------------------------------ review queue


@pytest.fixture
def review_id(client: TestClient, app_id: str, corpus_path) -> str:
    submit(client, app_id, corpus_path("identity_mismatch_axis"))
    queue = client.get("/reviews", headers=OPS).json()
    assert len(queue) == 1
    return queue[0]["id"]


def test_review_queue_requires_ops_credentials(client: TestClient) -> None:
    assert client.get("/reviews").status_code == 401


def test_the_review_card_carries_what_a_human_needs(client: TestClient, review_id: str) -> None:
    """A bare percentage gives a reviewer nothing to act on (ADR-009)."""
    card = client.get(f"/reviews/{review_id}", headers=OPS).json()

    assert card["reason_code"] == "IDENTITY_MISMATCH"
    assert card["application_reference"] == "FLX-44021"
    assert card["document_status"] == DocumentStatus.IN_REVIEW

    assert card["confidence_breakdown"]["composite"] is not None
    assert card["confidence_breakdown"]["rule_coverage"] is not None

    identity = next(r for r in card["rule_results"] if r["rule_id"] == "R-IDN-001")
    assert identity["evidence"]["account_holder"] == "RAJESH KUMAR SHARMA"
    assert identity["evidence"]["application_business_name"]

    assert card["extracted"]["account_number_masked"]
    assert card["download_url"]


def test_the_review_card_never_shows_a_full_account_number(
    client: TestClient, review_id: str
) -> None:
    raw = client.get(f"/reviews/{review_id}", headers=OPS).text
    assert "918020041627384" not in raw


def test_accepting_a_review_clears_the_requirement(
    client: TestClient, session: Session, app_id: str, review_id: str
) -> None:
    """A human accepting is a real clearance, and it counts as first-time.

    The customer sent a usable document on that submission; a human having to look at it
    is *our* uncertainty, not their rework. Counting it against them would make the
    metric measure our confidence rather than their experience.
    """
    response = client.post(
        f"/reviews/{review_id}/decision",
        headers=OPS,
        json={"action": "ACCEPT", "reviewer_id": "ops-anita", "note": "sole proprietor"},
    )
    assert response.status_code == 200
    assert response.json()["document_status"] == DocumentStatus.PASSED

    session.expire_all()
    requirement = session.scalars(
        select(DocumentRequirement).where(DocumentRequirement.application_id == app_id)
    ).one()
    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is True

    review = session.get(Review, review_id)
    assert review.status == ReviewStatus.RESOLVED
    assert review.reviewer_action == "ACCEPT"
    assert review.reviewer_id == "ops-anita"
    assert review.resolved_at is not None


def test_accepting_a_review_tells_the_customer_and_sales(
    client: TestClient, app_id: str, review_id: str
) -> None:
    client.post(
        f"/reviews/{review_id}/decision",
        headers=OPS,
        json={"action": "ACCEPT", "reviewer_id": "ops-anita"},
    )

    thread = customer_thread(client, app_id)
    assert any("has been verified" in m["body"] for m in thread)
    assert len(sales_thread(client, app_id)) == 1


def test_requesting_a_new_document_reopens_the_loop(
    client: TestClient, session: Session, app_id: str, review_id: str, corpus_path
) -> None:
    response = client.post(
        f"/reviews/{review_id}/decision",
        headers=OPS,
        json={"action": "REQUEST_NEW_DOCUMENT", "reviewer_id": "ops-anita"},
    )
    assert response.json()["document_status"] == DocumentStatus.NEEDS_FIX

    session.expire_all()
    requirement = session.scalars(
        select(DocumentRequirement).where(DocumentRequirement.application_id == app_id)
    ).one()
    assert requirement.status == RequirementStatus.AWAITING_CUSTOMER_ACTION

    assert "send it here" in customer_thread(client, app_id)[-1]["body"]

    # And the customer can correct it, exactly as with an automatic FIX.
    submit(client, app_id, corpus_path("valid_axis"))
    session.expire_all()
    requirement = session.scalars(
        select(DocumentRequirement).where(DocumentRequirement.application_id == app_id)
    ).one()
    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is False


def test_handoff_explains_a_human_override(client: TestClient, app_id: str, review_id: str) -> None:
    """A VERIFIED document with a failing check reads as a contradiction unless the
    override is stated. Credit needs to know a person made the call, and who."""
    client.post(
        f"/reviews/{review_id}/decision",
        headers=OPS,
        json={"action": "ACCEPT", "reviewer_id": "ops-anita", "note": "sole proprietor"},
    )

    validation = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()["documents"][0][
        "validation"
    ]

    assert validation["checks"]["Identity"] == "FAIL"
    override = validation["manual_review"]
    assert override["action"] == "ACCEPT"
    assert override["reviewer_id"] == "ops-anita"
    assert override["reason_code"] == "IDENTITY_MISMATCH"
    assert override["note"] == "sole proprietor"


def test_handoff_has_no_override_block_when_none_happened(
    client: TestClient, app_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))
    validation = client.get(f"/applications/{app_id}/handoff", headers=OPS).json()["documents"][0][
        "validation"
    ]
    assert "manual_review" not in validation


def test_a_review_cannot_be_decided_twice(client: TestClient, review_id: str) -> None:
    """Two reviewers opening the same card must not produce two decisions."""
    first = client.post(f"/reviews/{review_id}/decision", headers=OPS, json={"action": "ACCEPT"})
    assert first.status_code == 200

    second = client.post(f"/reviews/{review_id}/decision", headers=OPS, json={"action": "ACCEPT"})
    assert second.status_code == 409


def test_each_guard_against_double_resolution_works_independently(
    client: TestClient, session: Session, review_id: str
) -> None:
    """Resolution is guarded twice, and mutation testing showed only one of them was
    being exercised.

    The document-status guard catches the ordinary case (the document is terminal after
    the first decision). The review-status guard covers the case it cannot see: a review
    row already resolved while its document is somehow still awaiting one. They are
    layered deliberately, so each is asserted separately rather than trusting whichever
    fires first.
    """
    from app.domain.enums import ReviewerAction
    from app.services.review import ReviewNotOpenError, resolve

    review = session.get(Review, review_id)
    assert review is not None

    # Guard 1: review already resolved, document deliberately left IN_REVIEW.
    review.status = ReviewStatus.RESOLVED
    session.flush()
    with pytest.raises(ReviewNotOpenError, match="already"):
        resolve(session, review, action=ReviewerAction.ACCEPT)

    # Guard 2: review open, but the document is no longer awaiting a decision.
    review.status = ReviewStatus.OPEN
    document = session.get(Document, review.document_id)
    document.status = DocumentStatus.PASSED
    session.flush()
    with pytest.raises(ReviewNotOpenError, match="not awaiting review"):
        resolve(session, review, action=ReviewerAction.ACCEPT)


def test_an_invalid_action_is_rejected(client: TestClient, review_id: str) -> None:
    assert (
        client.post(
            f"/reviews/{review_id}/decision", headers=OPS, json={"action": "DELETE_EVERYTHING"}
        ).status_code
        == 422
    )


def test_resolved_reviews_leave_the_queue(client: TestClient, review_id: str) -> None:
    client.post(f"/reviews/{review_id}/decision", headers=OPS, json={"action": "ACCEPT"})
    assert client.get("/reviews", headers=OPS).json() == []


def test_the_queue_is_oldest_first(client: TestClient, corpus_path, review_id: str) -> None:
    """Working a review queue newest-first quietly strands the cases that have waited
    longest -- which is the pendency the product exists to cut.

    The second review is raised on a *different* application: sending another document
    to the same one would supersede the first submission and withdraw its review, which
    is correct behaviour and would make this test measure nothing.
    """
    second = client.post(
        "/applications",
        json={
            "external_reference": "FLX-44022",
            "borrower_name": "Another Borrower",
            "business_name": "Another Business Pvt Ltd",
            "phone_number": "+919820001235",
        },
    ).json()["id"]
    submit(client, second, corpus_path("balance_break_icici"))

    queue = client.get("/reviews", headers=OPS).json()
    assert len(queue) == 2
    assert queue[0]["id"] == review_id
    assert queue[0]["created_at"] <= queue[1]["created_at"]


def test_a_superseded_document_withdraws_its_open_review(
    client: TestClient, session: Session, app_id: str, review_id: str, corpus_path
) -> None:
    """Found by clicking through the Operations UI.

    A customer who sends a newer document while an older one is with Operations
    supersedes it. Without withdrawal the review stays OPEN forever, pointing at a
    document that is no longer the one the requirement depends on: the reviewer sees a
    card, decides on it, and gets an error. Worse, the queue silently looks busier than
    the work actually is, and oldest-first ordering floats the dead cards to the top.
    """
    submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    review = session.get(Review, review_id)
    assert review is not None
    assert review.status == ReviewStatus.WITHDRAWN
    assert review.reviewer_action is None, "nobody decided anything"
    assert "superseded" in (review.reviewer_note or "")

    assert client.get("/reviews", headers=OPS).json() == [], "the dead card left the queue"


def test_withdrawal_is_audited(
    client: TestClient, session: Session, app_id: str, review_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    events = session.scalars(
        select(DocumentEvent).where(DocumentEvent.event_type == EventType.REVIEW_WITHDRAWN)
    ).all()
    assert events
    assert any((e.payload or {}).get("review_id") == review_id for e in events)


def test_a_withdrawn_review_cannot_be_decided(
    client: TestClient, app_id: str, review_id: str, corpus_path
) -> None:
    submit(client, app_id, corpus_path("valid_hdfc"))

    response = client.post(f"/reviews/{review_id}/decision", headers=OPS, json={"action": "ACCEPT"})
    assert response.status_code == 409


def test_review_resolution_is_audited(client: TestClient, session: Session, review_id: str) -> None:
    client.post(
        f"/reviews/{review_id}/decision",
        headers=OPS,
        json={"action": "ACCEPT", "reviewer_id": "ops-anita"},
    )

    session.expire_all()
    events = session.scalars(
        select(DocumentEvent).where(DocumentEvent.event_type == EventType.REVIEW_RESOLVED)
    ).all()
    assert events
    assert any((e.payload or {}).get("reviewer_id") == "ops-anita" for e in events)


# ------------------------------------------------------------------ messaging provider


def test_recipients_are_masked_in_logs() -> None:
    """A phone number is personal data; the last four digits are enough to correlate."""
    assert _mask_recipient("+919820001234") == "***1234"
    assert _mask_recipient("12") == "***"


def test_the_simulated_provider_delivers_nothing_externally(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """WhatsApp is simulated on purpose: a real integration would put synthetic financial
    documents through a third-party processor we have not assessed
    (`RESEARCH_REGULATORY.md` §4)."""
    submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    messages = session.scalars(select(Message)).all()
    assert messages
    assert all(m.provider == SimulatedMessagingProvider.name for m in messages)


def test_message_bodies_are_not_written_to_the_audit_log(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """The event log has the longest retention of anything in the system, and the body
    is already stored and auditable elsewhere."""
    submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    events = session.scalars(
        select(DocumentEvent).where(
            DocumentEvent.event_type.in_([EventType.MESSAGE_SENT, EventType.SALES_NOTIFIED])
        )
    ).all()
    assert events
    for event in events:
        assert "body" not in (event.payload or {})
        assert "HDFC" not in str(event.payload)


def test_messages_survive_document_deletion(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """The conversation is part of the audit trail and must outlive a retention purge of
    the document content it describes."""
    document_id = submit(client, app_id, corpus_path("valid_hdfc"))

    session.expire_all()
    session.delete(session.get(Document, document_id))
    session.flush()

    assert session.scalars(select(Message).where(Message.document_id == document_id)).all(), (
        "the conversation was destroyed with the document"
    )


def test_channel_enum_covers_both_threads() -> None:
    assert {MessageChannel.CUSTOMER, MessageChannel.SALES} == set(MessageChannel)
