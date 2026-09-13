"""The only sanctioned way to move a document through its lifecycle.

Nothing else in the codebase assigns `document.status`. Routing every transition through
one function makes three guarantees structural rather than conventional:

1. Illegal transitions raise instead of silently corrupting state.
2. An audit event is appended for *every* transition -- it is not possible to move a
   document without leaving a record of how.
3. The requirement's status is recomputed in the same transaction, so the obligation and
   its submissions cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.domain.enums import (
    DocumentStatus,
    EventType,
    RequirementStatus,
    ReviewStatus,
)
from app.domain.state_machine import assert_transition, requirement_status_for
from app.models.application import Application, DocumentRequirement
from app.models.document import Document
from app.models.review import Review
from app.services.audit import record_event


def _context(session: Session, document: Document) -> tuple[DocumentRequirement, Application]:
    requirement = session.get(DocumentRequirement, document.requirement_id)
    if requirement is None:
        raise LookupError(f"requirement {document.requirement_id} not found")
    application = session.get(Application, requirement.application_id)
    if application is None:
        raise LookupError(f"application {requirement.application_id} not found")
    return requirement, application


def transition(
    session: Session,
    document: Document,
    target: DocumentStatus,
    *,
    event_type: EventType = EventType.STATUS_CHANGED,
    payload: dict[str, Any] | None = None,
) -> Document:
    """Move a document to `target`, auditing it and updating its requirement."""
    current = DocumentStatus(document.status)
    assert_transition(current, target)

    requirement, application = _context(session, document)

    document.status = target
    document.updated_at = utcnow()
    session.flush()

    record_event(
        session,
        event_type=event_type,
        document_id=document.id,
        requirement_id=requirement.id,
        application_id=application.id,
        application_reference=application.external_reference,
        from_status=current.value,
        to_status=target.value,
        payload=payload,
    )

    _sync_requirement(session, requirement, document, target)
    return document


def _sync_requirement(
    session: Session,
    requirement: DocumentRequirement,
    document: Document,
    target: DocumentStatus,
) -> None:
    if target is DocumentStatus.SUPERSEDED:
        # A superseded submission says nothing about the obligation; the newer
        # submission drives it.
        return

    if target is DocumentStatus.PASSED and requirement.status != RequirementStatus.CLEARED:
        requirement.status = RequirementStatus.CLEARED
        requirement.cleared_at = utcnow()
        requirement.cleared_by_document_id = document.id
        # The metric, fixed at the moment of clearance. Computing it later would break
        # once documents are purged under the retention policy.
        requirement.first_time_cleared = document.submission_index == 1
        session.flush()

        record_event(
            session,
            event_type=EventType.REQUIREMENT_CLEARED,
            document_id=document.id,
            requirement_id=requirement.id,
            application_id=requirement.application_id,
            payload={
                "submission_index": document.submission_index,
                "first_time_cleared": requirement.first_time_cleared,
                "submission_count": requirement.submission_count,
            },
        )
        return

    requirement.status = requirement_status_for(target)
    session.flush()


def supersede_active_submissions(
    session: Session, requirement: DocumentRequirement, *, except_document_id: str
) -> list[Document]:
    """Close out any in-flight submission when a newer one arrives.

    Customers re-send documents while the previous one is still processing -- an
    impatient second upload is normal behaviour, not an error. Without this, two
    submissions would race to set the requirement's status and the later verdict could
    be overwritten by the earlier one finishing afterwards.
    """
    active = session.scalars(
        select(Document).where(
            Document.requirement_id == requirement.id,
            Document.id != except_document_id,
            Document.status.notin_(
                [
                    DocumentStatus.PASSED,
                    DocumentStatus.NEEDS_FIX,
                    DocumentStatus.SUPERSEDED,
                ]
            ),
        )
    ).all()

    for doc in active:
        transition(
            session,
            doc,
            DocumentStatus.SUPERSEDED,
            event_type=EventType.DOCUMENT_SUPERSEDED,
            payload={"superseded_by": except_document_id},
        )
        _withdraw_open_reviews(session, doc, superseded_by=except_document_id)
    return list(active)


def _withdraw_open_reviews(session: Session, document: Document, *, superseded_by: str) -> None:
    """Close any review on a document that has just been superseded.

    Otherwise the Operations queue accumulates cards that can never be actioned: the
    reviewer sees a case, decides on it, and gets an error, because the document is no
    longer the one the requirement depends on. Worse, it is *silent* -- the queue looks
    busier than the work actually is, and the oldest-first ordering pushes the dead cards
    to the top.

    Withdrawn, not resolved: nobody decided anything. The question stopped being worth
    answering when the customer sent a newer document.
    """
    open_reviews = session.scalars(
        select(Review).where(Review.document_id == document.id, Review.status == ReviewStatus.OPEN)
    ).all()

    for review in open_reviews:
        review.status = ReviewStatus.WITHDRAWN
        review.reviewer_note = "withdrawn: superseded by a newer submission"
        review.resolved_at = utcnow()
        session.flush()

        record_event(
            session,
            event_type=EventType.REVIEW_WITHDRAWN,
            document_id=document.id,
            requirement_id=document.requirement_id,
            payload={"review_id": review.id, "superseded_by": superseded_by},
        )
