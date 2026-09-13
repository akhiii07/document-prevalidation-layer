"""Operations review resolution.

A review exists because the system declined to decide. Resolving one is therefore a
*human* decision being recorded, not a computation — so both outcomes are audited, both
are attributable, and both close the loop with the customer.

Accepting a reviewed document clears the requirement exactly as an automatic PASS would,
including for the first-time-clearance metric. That is deliberate: the customer sent a
usable document on that submission, and the fact that a human had to look at it is our
uncertainty, not their rework. Counting it against them would make the metric measure
our confidence rather than their experience.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.domain.enums import (
    DocumentStatus,
    EventType,
    MessageKind,
    Outcome,
    ReviewerAction,
    ReviewStatus,
)
from app.models.document import Document
from app.models.review import Review
from app.services.audit import record_event
from app.services.document_state import transition
from app.services.messages import compose_pass_summary
from app.services.notifications import application_for, notify_sales, record_message
from app.services.outcomes import _summary_for

logger = logging.getLogger("docverify.review")

ACCEPTED_MESSAGE = (
    "Good news — your bank statement has been verified.\n\nNo further action is required."
)

NEW_DOCUMENT_MESSAGE = (
    "Thanks for your patience. After checking your document, we need a fresh copy of "
    "your 6-month current-account bank statement.\n\n"
    "Please download it again from your bank and send it here."
)


class ReviewNotOpenError(RuntimeError):
    pass


def open_reviews(session: Session, *, limit: int = 50) -> list[Review]:
    return list(
        session.scalars(
            select(Review)
            .where(Review.status == ReviewStatus.OPEN)
            # Oldest first: a review queue worked newest-first quietly strands the cases
            # that have been waiting longest, which is the pendency we are trying to cut.
            .order_by(Review.created_at)
            .limit(limit)
        ).all()
    )


def resolve(
    session: Session,
    review: Review,
    *,
    action: ReviewerAction,
    reviewer_id: str | None = None,
    note: str | None = None,
) -> Review:
    if review.status != ReviewStatus.OPEN:
        raise ReviewNotOpenError(f"review {review.id} is already {review.status}")

    document = session.get(Document, review.document_id)
    if document is None:
        raise LookupError(f"document {review.document_id} not found")

    if DocumentStatus(document.status) is not DocumentStatus.IN_REVIEW:
        raise ReviewNotOpenError(
            f"document {document.id} is {document.status}, not awaiting review"
        )

    application, _ = application_for(session, document)

    target = DocumentStatus.PASSED if action is ReviewerAction.ACCEPT else DocumentStatus.NEEDS_FIX
    transition(
        session,
        document,
        target,
        event_type=EventType.REVIEW_RESOLVED,
        payload={
            "action": action.value,
            "reviewer_id": reviewer_id,
            "reason_code": review.reason_code,
        },
    )

    review.status = ReviewStatus.RESOLVED
    review.reviewer_action = action.value
    review.reviewer_id = reviewer_id
    review.reviewer_note = note
    review.resolved_at = utcnow()
    session.flush()

    record_event(
        session,
        event_type=EventType.REVIEW_RESOLVED,
        document_id=document.id,
        requirement_id=document.requirement_id,
        application_id=application.id,
        application_reference=application.external_reference,
        payload={"review_id": review.id, "action": action.value, "reviewer_id": reviewer_id},
    )

    body = ACCEPTED_MESSAGE if action is ReviewerAction.ACCEPT else NEW_DOCUMENT_MESSAGE
    record_message(
        session,
        application,
        body=body,
        kind=MessageKind.REVIEW_RESOLVED,
        document=document,
        context={
            "outcome": (Outcome.PASS if action is ReviewerAction.ACCEPT else Outcome.FIX).value,
            "resolved_by_human": True,
        },
    )

    if action is ReviewerAction.ACCEPT:
        summary = _summary_for(session, document)
        if summary:
            notify_sales(session, application, document, summary)
            record_message(
                session,
                application,
                body=compose_pass_summary(summary),
                kind=MessageKind.VALIDATION_RESULT,
                document=document,
                context={"outcome": Outcome.PASS.value, "resolved_by_human": True},
            )

    logger.info("review %s resolved: %s by %s", review.id, action.value, reviewer_id or "-")
    return review
