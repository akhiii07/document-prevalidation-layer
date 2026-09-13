"""Document lifecycle against the database.

The focus is the two properties later phases depend on: every transition leaves an audit
event, and First-Time Document Clearance Rate is computable from the schema.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus, EventType, MessageKind, RequirementStatus
from app.models.application import Application, DocumentRequirement
from app.models.event import DocumentEvent
from app.models.message import Message
from app.services.document_state import transition
from app.services.intake import create_submission, request_document

S = DocumentStatus


def _submit(session: Session, requirement: DocumentRequirement, name: str = "statement.pdf"):
    return create_submission(
        session,
        requirement,
        storage_key=f"docs/{name}-{requirement.submission_count + 1}",
        original_filename=name,
        file_size=1024,
        declared_mime_type="application/pdf",
    )


def _messages(session: Session, application_id: str) -> list[Message]:
    return list(
        session.scalars(
            select(Message)
            .where(Message.application_id == application_id)
            .order_by(Message.created_at)
        )
    )


def _events(session: Session, document_id: str) -> list[DocumentEvent]:
    return list(
        session.scalars(
            select(DocumentEvent)
            .where(DocumentEvent.document_id == document_id)
            .order_by(DocumentEvent.created_at, DocumentEvent.id)
        ).all()
    )


# ------------------------------------------------------------------ intake


def test_requirement_is_created_pending(session: Session, application: Application) -> None:
    """The denominator increments when Sales asks, not when a file arrives. A
    requirement nobody ever satisfies is exactly the pendency we are measuring."""
    requirement = request_document(session, application)
    assert requirement.status == RequirementStatus.PENDING
    assert requirement.submission_count == 0


def test_requesting_twice_does_not_create_two_requirements(
    session: Session, application: Application
) -> None:
    first = request_document(session, application)
    second = request_document(session, application)
    assert first.id == second.id


def test_submissions_are_indexed_from_one(
    session: Session, requirement: DocumentRequirement
) -> None:
    first = _submit(session, requirement)
    assert first.submission_index == 1

    transition(session, first, S.INGESTING)
    transition(session, first, S.NEEDS_FIX)

    second = _submit(session, requirement)
    assert second.submission_index == 2
    assert requirement.submission_count == 2


# ------------------------------------------------------------------ audit trail


def test_every_transition_appends_an_event(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submit(session, requirement)
    path = [S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.PASSED]
    for target in path:
        transition(session, document, target)

    status_events = [e for e in _events(session, document.id) if e.from_status is not None]
    assert [e.to_status for e in status_events] == [s.value for s in path]
    assert [e.from_status for e in status_events] == [
        S.RECEIVED.value,
        S.INGESTING.value,
        S.EXTRACTING.value,
        S.EXTRACTED.value,
        S.VALIDATING.value,
    ]


def test_events_carry_the_application_reference(
    session: Session, application: Application, requirement: DocumentRequirement
) -> None:
    """Denormalised so an event stays readable after the application row is purged."""
    document = _submit(session, requirement)
    transition(session, document, S.INGESTING)

    event = _events(session, document.id)[-1]
    assert event.application_reference == application.external_reference


def test_events_survive_document_deletion(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Content is purgeable under retention; the audit trail is not
    (`RESEARCH_REGULATORY.md` section 2). A cascade here would delete the record that
    has to outlive everything else."""
    document = _submit(session, requirement)
    transition(session, document, S.INGESTING)
    document_id = document.id

    session.delete(document)
    session.flush()

    assert _events(session, document_id), "audit events were destroyed with the document"


def test_audit_payloads_never_contain_a_password(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submit(session, requirement)
    transition(
        session,
        document,
        S.INGESTING,
        payload={"pdf_password": "SHAR1503", "note": "unlocking"},
    )

    payload = _events(session, document.id)[-1].payload
    assert payload is not None
    assert payload["pdf_password"] == "[redacted]"
    assert "SHAR1503" not in str(payload)


def test_audit_payloads_mask_long_digit_runs(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submit(session, requirement)
    transition(session, document, S.INGESTING, payload={"note": "account 50200047183926 read"})

    payload = _events(session, document.id)[-1].payload
    assert payload is not None
    assert "50200047183926" not in payload["note"]
    assert "3926" in payload["note"], "the tail is kept so the event stays useful"


# ------------------------------------------------------------------ the primary metric


def test_first_submission_clearing_counts_as_first_time(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submit(session, requirement)
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.PASSED):
        transition(session, document, target)

    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is True
    assert requirement.cleared_by_document_id == document.id
    assert requirement.cleared_at is not None


def test_correction_loop_clears_but_not_first_time(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Scenario 2 from PRODUCT_SPEC section 10: FIX, re-upload, PASS.

    The requirement is cleared, but *not* first time -- and the denominator is still
    one requirement, not two documents. Getting this wrong would make the headline
    metric improve as rework increased."""
    first = _submit(session, requirement, "four-months.pdf")
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.NEEDS_FIX):
        transition(session, first, target)

    assert requirement.status == RequirementStatus.AWAITING_CUSTOMER_ACTION

    second = _submit(session, requirement, "six-months.pdf")
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.PASSED):
        transition(session, second, target)

    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is False
    assert requirement.submission_count == 2


def test_clearance_records_an_event_with_the_metric(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submit(session, requirement)
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.PASSED):
        transition(session, document, target)

    cleared = [
        e for e in _events(session, document.id) if e.event_type == EventType.REQUIREMENT_CLEARED
    ]
    assert len(cleared) == 1
    assert cleared[0].payload == {
        "submission_index": 1,
        "first_time_cleared": True,
        "submission_count": 1,
    }


def test_review_then_accept_still_clears_the_requirement(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Operations accepting a REVIEW is a real clearance and must be counted as one."""
    document = _submit(session, requirement)
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.IN_REVIEW):
        transition(session, document, target)
    assert requirement.status == RequirementStatus.AWAITING_REVIEW

    transition(session, document, S.PASSED, event_type=EventType.REVIEW_RESOLVED)
    assert requirement.status == RequirementStatus.CLEARED
    assert requirement.first_time_cleared is True


# ------------------------------------------------------------------ supersession


def test_a_new_submission_supersedes_one_still_in_flight(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Impatient customers re-send while the first attempt is still processing. Without
    supersession two verdicts would race and the earlier one could land last."""
    first = _submit(session, requirement, "first.pdf")
    transition(session, first, S.INGESTING)

    second = _submit(session, requirement, "second.pdf")

    session.refresh(first)
    assert first.status == S.SUPERSEDED
    assert second.status == S.RECEIVED
    assert requirement.status == RequirementStatus.IN_PROGRESS


def test_supersession_does_not_touch_finished_submissions(
    session: Session, requirement: DocumentRequirement
) -> None:
    first = _submit(session, requirement, "first.pdf")
    for target in (S.INGESTING, S.EXTRACTING, S.EXTRACTED, S.VALIDATING, S.NEEDS_FIX):
        transition(session, first, target)

    _submit(session, requirement, "second.pdf")

    session.refresh(first)
    assert first.status == S.NEEDS_FIX, "a finished submission keeps its verdict"


def test_the_customer_is_told_their_document_was_dropped(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Supersession is invisible to the customer unless we say so.

    Someone testing this uploaded their statement, grew impatient, tried a sample while
    the first was still processing, and then read the *sample's* verdict as a judgement
    on their own document. Nothing in the thread said the first file had been dropped,
    so there was no way to tell the two apart.
    """
    first = _submit(session, requirement, "my-statement.pdf")
    transition(session, first, S.INGESTING)

    _submit(session, requirement, "sample.pdf")

    notice = next(
        message
        for message in _messages(session, requirement.application_id)
        if message.kind == MessageKind.SUBMISSION_SUPERSEDED
    )
    assert notice.document_id == first.id, "the notice must name the dropped document"
    assert "my-statement.pdf" in notice.body
    assert "sample.pdf" in notice.body


def test_every_message_about_a_document_names_it(
    session: Session, requirement: DocumentRequirement
) -> None:
    """Attribution is stamped centrally so no message can be written without it."""
    document = _submit(session, requirement, "statement.pdf")
    messages = [
        message
        for message in _messages(session, requirement.application_id)
        if message.document_id == document.id
    ]
    assert messages
    assert all((message.context or {}).get("filename") == "statement.pdf" for message in messages)


def test_superseded_submission_is_audited(
    session: Session, requirement: DocumentRequirement
) -> None:
    first = _submit(session, requirement, "first.pdf")
    transition(session, first, S.INGESTING)
    second = _submit(session, requirement, "second.pdf")

    events = [
        e for e in _events(session, first.id) if e.event_type == EventType.DOCUMENT_SUPERSEDED
    ]
    assert len(events) == 1
    assert events[0].payload == {"superseded_by": second.id}
