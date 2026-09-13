"""Document upload, status, password, and download."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_ops_secret
from app.api.schemas import (
    DocumentRead,
    DocumentStatusRead,
    DownloadUrlRead,
    PasswordSubmit,
    ValidationRead,
)
from app.config.rules import get_rules
from app.db.session import get_db
from app.domain.enums import DocumentStatus, JobType
from app.models.application import Application
from app.models.document import Document
from app.models.validation import ValidationResult
from app.providers.queue import get_queue
from app.providers.storage import get_storage
from app.services.ingestion import ingest
from app.services.password import get_password_vault
from app.services.signed_urls import InvalidTokenError, issue_token, verify_token

logger = logging.getLogger("docverify.api")

router = APIRouter(tags=["documents"])


def _latest_validation(session: Session, document_id: str) -> ValidationResult | None:
    return session.scalars(
        select(ValidationResult)
        .where(ValidationResult.document_id == document_id)
        .order_by(ValidationResult.created_at.desc())
        .limit(1)
    ).first()


def _get_document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.post(
    "/applications/{application_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_document(
    application_id: str,
    file: UploadFile = File(...),
    document_type: str = Query(default="bank_statement"),
    session: Session = Depends(get_db),
) -> DocumentRead:
    """Accept a submission and queue it.

    202, not 200: nothing has been decided yet. Returning a verdict here would mean
    running the checks inside the request, bypassing the retry and dead-letter guarantees
    that make a verdict reliable. The client polls `/documents/{id}/status`.
    """
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    document, _ = ingest(
        session,
        application,
        source=file.file,
        # Retained only to quote back to the customer; never used to build a path.
        original_filename=file.filename or "upload.pdf",
        declared_mime_type=file.content_type,
        document_type=document_type,
    )
    return DocumentRead.model_validate(document)


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: str, session: Session = Depends(get_db)) -> DocumentRead:
    document = _get_document(session, document_id)
    payload = DocumentRead.model_validate(document)
    result = _latest_validation(session, document_id)
    if result is not None:
        payload.validation = ValidationRead.model_validate(result)
    return payload


@router.get("/documents/{document_id}/status", response_model=DocumentStatusRead)
def get_document_status(document_id: str, session: Session = Depends(get_db)) -> DocumentStatusRead:
    document = _get_document(session, document_id)
    result = _latest_validation(session, document_id)
    rules = get_rules()

    awaiting = DocumentStatus(document.status) is DocumentStatus.AWAITING_PASSWORD
    return DocumentStatusRead(
        id=document.id,
        status=document.status,
        outcome=result.outcome if result else None,
        reason_code=result.primary_reason_code if result else None,
        customer_message=result.customer_message if result else None,
        awaiting_password=awaiting,
        password_attempts_remaining=(
            max(0, rules.file.max_password_attempts - document.password_attempts)
            if awaiting
            else None
        ),
    )


@router.post("/documents/{document_id}/password", status_code=status.HTTP_202_ACCEPTED)
def submit_password(
    document_id: str,
    payload: PasswordSubmit,
    session: Session = Depends(get_db),
) -> dict[str, str]:
    """Supply the PDF password.

    The value is never logged, never written to the database, and not hashed
    (`RESEARCH_REGULATORY.md` section 3). It is handed to a process-local, single-use,
    expiring vault (ADR-015) and consumed by the worker within seconds.
    """
    document = _get_document(session, document_id)

    if DocumentStatus(document.status) is not DocumentStatus.AWAITING_PASSWORD:
        raise HTTPException(
            status_code=409,
            detail=f"document is not awaiting a password (status: {document.status})",
        )

    rules = get_rules()
    if document.password_attempts >= rules.file.max_password_attempts:
        raise HTTPException(status_code=429, detail="too many password attempts")

    get_password_vault().put(document_id, payload.password.get_secret_value())

    get_queue().enqueue(
        session,
        JobType.PROCESS_DOCUMENT,
        {"document_id": document_id},
        # A new attempt is new work: the dedupe key includes the attempt number so a
        # retry is not swallowed as a duplicate of the run that just failed.
        dedupe_key=f"document:{document_id}:pw:{document.password_attempts}",
    )
    # Deliberately no logging of the request body anywhere on this path.
    logger.info("password received for document %s", document_id)
    return {"status": "accepted"}


@router.get(
    "/documents/{document_id}/download-url",
    response_model=DownloadUrlRead,
    dependencies=[Depends(require_ops_secret)],
)
def create_download_url(document_id: str, session: Session = Depends(get_db)) -> DownloadUrlRead:
    """Mint a short-lived signed URL. Requires Operations credentials."""
    document = _get_document(session, document_id)
    token, expires_at = issue_token(document.id)
    return DownloadUrlRead(
        url=f"/documents/{document.id}/download?token={token}",
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    token: str = Query(...),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream a document to a holder of a valid, unexpired token.

    The token is the only credential: it is bound to this document id and expires. There
    is no unauthenticated path to a stored object, and no public URL exists at all.
    """
    try:
        verify_token(document_id, token)
    except InvalidTokenError as exc:
        # One message for expired, forged and malformed alike, so the response cannot be
        # used to distinguish them.
        raise HTTPException(status_code=403, detail="invalid or expired download token") from exc

    document = _get_document(session, document_id)
    storage = get_storage()
    if not storage.exists(document.storage_key):
        raise HTTPException(status_code=410, detail="document content is no longer available")

    return StreamingResponse(
        storage.open(document.storage_key),
        media_type="application/pdf",
        headers={
            # The customer's own filename is echoed back, quoted, so it is never
            # interpreted as a path.
            "Content-Disposition": f'attachment; filename="{document.original_filename}"',
            "Cache-Control": "no-store",
        },
    )
