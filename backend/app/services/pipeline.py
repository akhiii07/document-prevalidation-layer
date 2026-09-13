"""The document processing pipeline.

Stages: file and security checks, the password flow, then extraction. Validation
(Phase 7) continues from `EXTRACTED`.

The handler is state-aware rather than linear, because the pipeline is genuinely
re-entrant: a document that stopped at `AWAITING_PASSWORD` resumes days later when the
customer replies, and the same job type drives both passes.
"""

from __future__ import annotations

import io
import logging
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.config.rules import get_rules
from app.domain.enums import DocumentStatus, EventType, Outcome
from app.domain.state_machine import is_terminal
from app.models.document import Document
from app.models.job import Job
from app.providers.storage import StorageProvider, get_storage
from app.services.audit import record_event
from app.services.document_state import transition
from app.services.extraction import run_extraction
from app.services.outcomes import record_outcome, request_password
from app.services.password import WrongPasswordError, get_password_vault, unlock
from app.services.validation import run_validation
from app.validators.file_validator import check_file
from app.validators.integrity import collect_pdf_signals

logger = logging.getLogger("docverify.pipeline")


def process_document(session: Session, job: Job) -> None:
    """Entry point registered for `PROCESS_DOCUMENT` jobs."""
    document_id = (job.payload or {}).get("document_id")
    if not document_id:
        raise ValueError("PROCESS_DOCUMENT job has no document_id")

    document = session.get(Document, document_id)
    if document is None:
        raise LookupError(f"document {document_id} not found")

    status = DocumentStatus(document.status)
    if is_terminal(status):
        # A superseded or already-decided submission. Not an error: a job can outlive
        # the thing it was queued for.
        logger.info("document %s already in %s; nothing to do", document.id, status)
        return

    if status is DocumentStatus.AWAITING_PASSWORD:
        _resume_after_password(session, document)
        return

    if status is DocumentStatus.RECEIVED:
        transition(session, document, DocumentStatus.INGESTING)

    _run_file_checks(session, document)


# ------------------------------------------------------------------ stages


def _open(document: Document, storage: StorageProvider | None = None) -> BinaryIO:
    storage = storage or get_storage()
    return io.BytesIO(storage.read(document.storage_key))


def _run_file_checks(
    session: Session,
    document: Document,
    *,
    stream: BinaryIO | None = None,
    size: int | None = None,
    storage: StorageProvider | None = None,
) -> None:
    rules = get_rules()
    owned = stream is None
    stream = stream or _open(document, storage)

    try:
        result = check_file(
            stream,
            size=document.file_size if size is None else size,
            rules=rules,
            declared_mime_type=document.declared_mime_type,
        )

        if result.detected_mime_type:
            document.detected_mime_type = result.detected_mime_type
            session.flush()

        if result.requires_password:
            request_password(session, document)
            return

        if result.failed:
            record_outcome(
                session,
                document,
                outcome=Outcome.FIX,
                reason_code=result.reason_code,
                evidence=result.evidence,
                rule_results=[
                    {
                        "code": result.reason_code,
                        "status": "FAIL",
                        "evidence": result.evidence,
                    }
                ],
            )
            return

        transition(
            session,
            document,
            DocumentStatus.EXTRACTING,
            payload={"page_count": result.page_count},
        )
        # The already-open stream is reused. Re-reading from storage would work, but
        # for a decrypted document there is nothing on disk to re-read: the plaintext
        # exists only here, in memory (ADR-015).
        stream.seek(0)
        # Container-level integrity signals are collected while the bytes are to hand;
        # after extraction the PDF structure is no longer available.
        signals = collect_pdf_signals(stream, rules)

        stream.seek(0)
        statement = run_extraction(session, document, stream)
        if statement is not None:
            run_validation(session, document, statement, signals)
    finally:
        if owned:
            stream.close()


def _resume_after_password(session: Session, document: Document) -> None:
    """Continue a document that was waiting on its password.

    The password is taken from the process-local vault (ADR-015) and removed in the same
    call, so it cannot be reused after this run. If it is gone -- expired, or the process
    restarted -- the customer is asked again rather than the document being failed,
    because nothing is wrong with the document.
    """
    vault = get_password_vault()
    password = vault.take(document.id)

    if password is None:
        logger.info("document %s has no pending password; waiting for the customer", document.id)
        return

    rules = get_rules()
    try:
        plaintext = unlock(_open(document), password)
    except WrongPasswordError:
        document.password_attempts += 1
        session.flush()

        attempts_left = rules.file.max_password_attempts - document.password_attempts
        if attempts_left <= 0:
            record_outcome(
                session,
                document,
                outcome=Outcome.FIX,
                reason_code="FILE_PASSWORD_INCORRECT",
                evidence={"attempts": document.password_attempts},
            )
            return

        record_event(
            session,
            event_type=EventType.PASSWORD_REJECTED,
            document_id=document.id,
            requirement_id=document.requirement_id,
            payload={"attempts": document.password_attempts, "attempts_left": attempts_left},
        )
        return
    finally:
        # Drop the reference promptly. The vault entry is already gone; this removes the
        # last handle the pipeline holds.
        del password

    record_event(
        session,
        event_type=EventType.PASSWORD_ACCEPTED,
        document_id=document.id,
        requirement_id=document.requirement_id,
        payload={"attempts": document.password_attempts + 1},
    )

    # The decrypted bytes exist only for this call and are never written to storage
    # (`RESEARCH_REGULATORY.md` section 3). Only the encrypted original is retained.
    # AWAITING_PASSWORD -> EXTRACTING happens inside the checks, so the decrypted content
    # is verified to be a usable PDF before the document is declared ready to extract.
    _run_file_checks(session, document, stream=plaintext, size=plaintext.getbuffer().nbytes)
