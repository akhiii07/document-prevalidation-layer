"""Applications and their document requirements.

Applications are seeded here rather than integrated with a real LOS (`PRODUCT_SPEC.md`
section 5). In the intended journey Sales fills the application and asks the customer to
send the statement to a WhatsApp number; this endpoint stands in for that step.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_ops_secret
from app.api.schemas import (
    ApplicationCreate,
    ApplicationRead,
    ApplicationSummary,
    DocumentRead,
    HandoffDocument,
    HandoffRead,
    MessageRead,
)
from app.db.session import get_db
from app.domain.enums import DocumentStatus, MessageChannel, ReviewStatus
from app.models.application import Application, DocumentRequirement
from app.models.document import Document, DocumentExtraction
from app.models.review import Review
from app.models.validation import ValidationResult
from app.services.intake import create_application, request_document
from app.services.notifications import thread
from app.services.signed_urls import issue_token

router = APIRouter(tags=["applications"])


@router.post("/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create(payload: ApplicationCreate, session: Session = Depends(get_db)) -> ApplicationRead:
    try:
        application = create_application(
            session,
            external_reference=payload.external_reference,
            borrower_name=payload.borrower_name,
            business_name=payload.business_name,
            phone_number=payload.phone_number,
        )
        # Sales asking for the document is what creates the requirement -- and the
        # requirement is the denominator of First-Time Document Clearance Rate, so it
        # exists from this moment, not from the moment a file arrives.
        request_document(session, application)
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="an application with that reference already exists"
        ) from exc

    session.refresh(application)
    return ApplicationRead.model_validate(application)


@router.get(
    "/applications",
    response_model=list[ApplicationSummary],
    dependencies=[Depends(require_ops_secret)],
)
def list_applications(
    limit: int = Query(default=50, le=200), session: Session = Depends(get_db)
) -> list[ApplicationSummary]:
    """Every application, newest first.

    Ops-gated because it is a list of borrowers -- names, businesses and their document
    status. That is exactly the kind of enumeration that should not be open, even in a
    prototype (ADR-011).
    """
    applications = session.scalars(
        select(Application).order_by(Application.created_at.desc()).limit(limit)
    ).all()

    summaries: list[ApplicationSummary] = []
    for application in applications:
        requirement = application.requirements[0] if application.requirements else None
        verified = 0
        if requirement is not None:
            verified = sum(1 for d in requirement.documents if d.status == DocumentStatus.PASSED)
        summaries.append(
            ApplicationSummary(
                id=application.id,
                external_reference=application.external_reference,
                borrower_name=application.borrower_name,
                business_name=application.business_name,
                status=application.status,
                created_at=application.created_at,
                requirement_status=requirement.status if requirement else None,
                submission_count=requirement.submission_count if requirement else 0,
                first_time_cleared=requirement.first_time_cleared if requirement else None,
                verified_documents=verified,
            )
        )
    return summaries


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def read(application_id: str, session: Session = Depends(get_db)) -> ApplicationRead:
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return ApplicationRead.model_validate(application)


@router.get("/applications/{application_id}/documents", response_model=list[DocumentRead])
def list_documents(application_id: str, session: Session = Depends(get_db)) -> list[DocumentRead]:
    """Every submission against this application, oldest first.

    Submissions are listed rather than collapsed to the latest one: the correction loop
    is part of the story, and Sales needs to see that a document was corrected, not just
    that it eventually passed.
    """
    if session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")

    documents = session.scalars(
        select(Document)
        .join(DocumentRequirement)
        .where(DocumentRequirement.application_id == application_id)
        .order_by(Document.created_at, Document.submission_index)
    ).all()
    return [DocumentRead.model_validate(d) for d in documents]


@router.get("/applications/{application_id}/messages", response_model=list[MessageRead])
def list_messages(
    application_id: str,
    channel: str = Query(default=MessageChannel.CUSTOMER.value),
    session: Session = Depends(get_db),
) -> list[MessageRead]:
    """The conversation thread.

    This is what the simulated WhatsApp screen renders. It is stored rather than
    reconstructed from validation rows, so the transcript is exactly what was said, in
    the order it was said -- which is also what an auditor would ask for.
    """
    if session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")

    try:
        selected = MessageChannel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown channel") from exc

    return [MessageRead.model_validate(m) for m in thread(session, application_id, selected)]


@router.get(
    "/applications/{application_id}/handoff",
    response_model=HandoffRead,
    dependencies=[Depends(require_ops_secret)],
)
def handoff(application_id: str, session: Session = Depends(get_db)) -> HandoffRead:
    """What the product would hand to the lender's LOS (`PRODUCT_SPEC.md` section 21).

    Deliberately a read-only payload rather than a push integration: the MVP proves the
    document is ready and describes it precisely. Wiring it into a real LOS is an
    integration exercise that would demonstrate nothing about the hypothesis.
    """
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    requirement = session.scalars(
        select(DocumentRequirement).where(DocumentRequirement.application_id == application_id)
    ).first()
    if requirement is None:
        raise HTTPException(status_code=404, detail="no document requirement for this application")

    verified = session.scalars(
        select(Document)
        .where(
            Document.requirement_id == requirement.id,
            Document.status == DocumentStatus.PASSED,
        )
        .order_by(Document.submission_index)
    ).all()

    documents: list[HandoffDocument] = []
    for document in verified:
        extraction = session.scalars(
            select(DocumentExtraction)
            .where(DocumentExtraction.document_id == document.id)
            .order_by(DocumentExtraction.created_at.desc())
            .limit(1)
        ).first()
        result = session.scalars(
            select(ValidationResult)
            .where(ValidationResult.document_id == document.id)
            .order_by(ValidationResult.created_at.desc())
            .limit(1)
        ).first()
        data = (extraction.normalized_data if extraction else None) or {}
        token, _ = issue_token(document.id)

        documents.append(
            HandoffDocument(
                document_id=document.id,
                status="VERIFIED",
                submission_index=document.submission_index,
                verified_at=document.updated_at,
                bank_name=data.get("bank_name"),
                account_holder_name=data.get("account_holder_name"),
                account_number_masked=data.get("account_number_masked"),
                account_type=data.get("account_type"),
                period_start=data.get("period_start"),
                period_end=data.get("period_end"),
                page_count=data.get("page_count"),
                transaction_count=data.get("transaction_count"),
                validation=_validation_summary(session, document, result),
                download_url=f"/documents/{document.id}/download?token={token}",
            )
        )

    return HandoffRead(
        application_reference=application.external_reference,
        borrower_name=application.borrower_name,
        business_name=application.business_name,
        requirement_status=requirement.status,
        first_time_cleared=requirement.first_time_cleared,
        submission_count=requirement.submission_count,
        documents=documents,
    )


def _validation_summary(
    session: Session, document: Document, result: ValidationResult | None
) -> dict:
    """The per-check table Sales and Credit see, rebuilt from the stored rule results."""
    if result is None:
        return {}

    checks: dict[str, str] = {}
    for rule in result.rule_results or []:
        label = _RULE_LABELS.get(rule["rule_id"])
        # A family fails if any of its rules failed, so a recorded FAIL is never
        # overwritten by a later PASS in the same family.
        if label and rule["status"] in {"PASS", "FAIL"} and checks.get(label) != "FAIL":
            checks[label] = rule["status"]

    summary = {
        "outcome": result.outcome,
        "checks": checks,
        "confidence": result.composite_confidence,
        "rules_version": result.rules_version,
    }

    # A document can be VERIFIED with a failing check when a human overrode it -- an
    # identity mismatch on a sole proprietor's account is the common case. Left
    # unexplained the table reads as a contradiction, so the override is stated
    # explicitly. Credit needs to know a person made this call, and who.
    review = session.scalars(
        select(Review)
        .where(Review.document_id == document.id, Review.status == ReviewStatus.RESOLVED)
        .order_by(Review.resolved_at.desc())
        .limit(1)
    ).first()
    if review is not None:
        summary["manual_review"] = {
            "reason_code": review.reason_code,
            "action": review.reviewer_action,
            "reviewer_id": review.reviewer_id,
            "note": review.reviewer_note,
            "resolved_at": review.resolved_at.isoformat() if review.resolved_at else None,
        }

    return summary


_RULE_LABELS = {
    "R-DOC-001": "Document Type",
    "R-DOC-002": "Account Type",
    "R-PER-001": "Period",
    "R-PER-002": "Period",
    "R-CMP-001": "Completeness",
    "R-CMP-002": "Completeness",
    "R-CMP-004": "Completeness",
    "R-RED-001": "Readability",
    "R-IDN-001": "Identity",
}
