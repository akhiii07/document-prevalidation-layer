"""Operations review queue.

Minimal by design (`PRODUCT_SPEC.md` §20): its purpose is to demonstrate REVIEW handling,
not to be an operations platform. What it must get right is the card — a reviewer needs
the reason, the confidence *breakdown*, and the extracted summary, because a bare
percentage gives them nothing to act on (ADR-009).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_ops_secret
from app.api.schemas import ReviewDecision, ReviewDetail, ReviewSummary
from app.db.session import get_db
from app.domain.enums import ReviewerAction
from app.models.application import Application, DocumentRequirement
from app.models.document import Document, DocumentExtraction
from app.models.review import Review
from app.models.validation import ValidationResult
from app.services.review import ReviewNotOpenError, open_reviews, resolve
from app.services.signed_urls import issue_token

router = APIRouter(tags=["operations"], dependencies=[Depends(require_ops_secret)])


def _context(session: Session, review: Review):
    document = session.get(Document, review.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    requirement = session.get(DocumentRequirement, document.requirement_id)
    application = session.get(Application, requirement.application_id) if requirement else None
    if requirement is None or application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return document, requirement, application


def _summary(session: Session, review: Review) -> ReviewSummary:
    document, _, application = _context(session, review)
    return ReviewSummary(
        id=review.id,
        document_id=document.id,
        application_reference=application.external_reference,
        borrower_name=application.borrower_name,
        business_name=application.business_name,
        reason_code=review.reason_code,
        confidence=review.confidence,
        submission_index=document.submission_index,
        original_filename=document.original_filename,
        created_at=review.created_at,
    )


@router.get("/reviews", response_model=list[ReviewSummary])
def list_reviews(
    limit: int = Query(default=50, le=200), session: Session = Depends(get_db)
) -> list[ReviewSummary]:
    return [_summary(session, review) for review in open_reviews(session, limit=limit)]


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
def read_review(review_id: str, session: Session = Depends(get_db)) -> ReviewDetail:
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    document, _, _ = _context(session, review)
    result = session.scalars(
        select(ValidationResult)
        .where(ValidationResult.document_id == document.id)
        .order_by(ValidationResult.created_at.desc())
        .limit(1)
    ).first()
    extraction = session.scalars(
        select(DocumentExtraction)
        .where(DocumentExtraction.document_id == document.id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(1)
    ).first()

    breakdown = (
        {
            "composite": result.composite_confidence,
            "field_score": result.field_score,
            "rule_coverage": result.rule_coverage,
        }
        if result is not None
        else None
    )

    token, _ = issue_token(document.id)
    return ReviewDetail(
        **_summary(session, review).model_dump(),
        status=review.status,
        document_status=document.status,
        # The whole point of the card: which rules ran, which failed, and on what
        # evidence. A reviewer who only sees a score cannot do anything with it.
        rule_results=(result.rule_results if result else None),
        confidence_breakdown=breakdown,
        # Already masked at the source: `normalized_data` never contains a full account
        # number (`PRODUCT_SPEC.md` §12).
        extracted=(extraction.normalized_data if extraction else None),
        customer_message=(result.customer_message if result else None),
        download_url=f"/documents/{document.id}/download?token={token}",
    )


@router.post("/reviews/{review_id}/decision", response_model=ReviewDetail)
def decide(
    review_id: str, payload: ReviewDecision, session: Session = Depends(get_db)
) -> ReviewDetail:
    """Record a human decision. Both outcomes are audited and both reach the customer."""
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    try:
        resolve(
            session,
            review,
            action=ReviewerAction(payload.action),
            reviewer_id=payload.reviewer_id,
            note=payload.note,
        )
    except ReviewNotOpenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    session.flush()
    return read_review(review_id, session)
