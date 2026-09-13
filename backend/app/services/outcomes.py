"""Recording a verdict.

One function writes the `validation_results` row, transitions the document, opens a
review where needed, and audits all of it. Phase 7 will feed it richer rule evidence;
the shape of what gets written does not change.

Customer message composition lives in `services/messages.py`; this module is only
responsible for making sure every verdict carries one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.rules import get_rules
from app.domain.enums import DocumentStatus, EventType, MessageKind, Outcome, ReviewStatus
from app.domain.state_machine import status_for_outcome
from app.models.document import Document
from app.models.review import Review
from app.models.validation import ValidationResult
from app.services.document_state import transition
from app.services.messages import PASSWORD_PROMPT, compose_customer_message
from app.services.notifications import (
    application_for,
    notify_sales,
    record_message,
)


def record_outcome(
    session: Session,
    document: Document,
    *,
    outcome: Outcome,
    reason_code: str | None = None,
    secondary_reason_codes: list[str] | None = None,
    rule_results: list[dict[str, Any]] | None = None,
    composite_confidence: float | None = None,
    field_score: float | None = None,
    rule_coverage: float | None = None,
    message: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> ValidationResult:
    rules = get_rules()

    if reason_code is not None and reason_code not in rules.known_reason_codes:
        # A verdict carrying a code the catalogue does not define cannot be explained to
        # anyone, which defeats the point of having a reason at all.
        raise ValueError(f"unknown reason code: {reason_code}")

    if message is None and outcome is not Outcome.PASS:
        message = compose_customer_message(outcome, reason_code, evidence)

    result = ValidationResult(
        document_id=document.id,
        outcome=outcome,
        primary_reason_code=reason_code,
        secondary_reason_codes=secondary_reason_codes,
        rule_results=rule_results,
        composite_confidence=composite_confidence,
        field_score=field_score,
        rule_coverage=rule_coverage,
        customer_message=message,
        rules_version=rules.version,
    )
    session.add(result)
    session.flush()

    target = status_for_outcome(outcome)
    event_type = {
        Outcome.PASS: EventType.VALIDATION_COMPLETED,
        Outcome.FIX: EventType.FIX_REQUESTED,
        Outcome.REVIEW: EventType.REVIEW_OPENED,
    }[outcome]

    transition(
        session,
        document,
        target,
        event_type=event_type,
        payload={
            "outcome": outcome.value,
            "reason_code": reason_code,
            "confidence": composite_confidence,
            "evidence": evidence,
        },
    )

    if outcome is Outcome.REVIEW:
        session.add(
            Review(
                document_id=document.id,
                reason_code=reason_code or "EXTRACTION_LOW_CONFIDENCE",
                confidence=composite_confidence,
                status=ReviewStatus.OPEN,
            )
        )
        session.flush()

    # Every verdict reaches the customer. Routing this through `record_outcome` rather
    # than each caller makes that structural: a verdict cannot be recorded without the
    # customer being told.
    application, _ = application_for(session, document)
    if message:
        record_message(
            session,
            application,
            body=message,
            kind=MessageKind.VALIDATION_RESULT,
            document=document,
            context={"outcome": outcome.value, "reason_code": reason_code},
        )

    if outcome is Outcome.PASS:
        notify_sales(session, application, document, _summary_for(session, document))

    return result


def _summary_for(session: Session, document: Document) -> dict[str, Any]:
    """The normalised statement, for the Sales notification."""
    from app.models.document import DocumentExtraction

    extraction = session.scalars(
        select(DocumentExtraction)
        .where(DocumentExtraction.document_id == document.id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(1)
    ).first()
    return dict(extraction.normalized_data or {}) if extraction else {}


def request_password(session: Session, document: Document) -> None:
    """Ask for the PDF password. This is a gate, not a verdict.

    No `validation_results` row is written: nothing is wrong with the document, so
    recording a failed validation would corrupt the failure-reason distribution and make
    encrypted statements look like defects in the metrics.
    """
    document.is_encrypted = True
    transition(
        session,
        document,
        DocumentStatus.AWAITING_PASSWORD,
        event_type=EventType.PASSWORD_REQUESTED,
        payload={"reason_code": "FILE_PASSWORD_REQUIRED"},
    )

    application, _ = application_for(session, document)
    record_message(
        session,
        application,
        body=PASSWORD_PROMPT,
        kind=MessageKind.PASSWORD_REQUEST,
        document=document,
        context={"reason_code": "FILE_PASSWORD_REQUIRED"},
    )
