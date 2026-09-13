"""Creating applications, requirements, and submissions.

File handling proper (magic bytes, size caps, storage, the password flow) arrives in
Phase 5. What lives here is the bookkeeping those steps depend on: getting a submission
correctly attached to its requirement with the right index, because the primary metric
is computed from exactly that.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ApplicationStatus, EventType, RequirementStatus
from app.models.application import Application, DocumentRequirement
from app.models.document import Document
from app.services.audit import record_event
from app.services.document_state import supersede_active_submissions
from app.services.notifications import notify_superseded, record_inbound_document, send_welcome

DEFAULT_DOCUMENT_TYPE = "bank_statement"


def create_application(
    session: Session,
    *,
    external_reference: str,
    borrower_name: str,
    business_name: str,
    phone_number: str,
) -> Application:
    application = Application(
        external_reference=external_reference,
        borrower_name=borrower_name,
        business_name=business_name,
        phone_number=phone_number,
        status=ApplicationStatus.OPEN,
    )
    session.add(application)
    session.flush()
    return application


def request_document(
    session: Session,
    application: Application,
    *,
    document_type: str = DEFAULT_DOCUMENT_TYPE,
) -> DocumentRequirement:
    """Record that Sales has asked the customer for a document.

    This is the moment the metric's denominator increments -- not when a file arrives.
    A requirement that is never satisfied is precisely the pendency the product exists
    to reduce, so it has to be counted even if nothing is ever submitted against it.
    """
    existing = session.scalars(
        select(DocumentRequirement).where(
            DocumentRequirement.application_id == application.id,
            DocumentRequirement.document_type == document_type,
        )
    ).first()
    if existing is not None:
        return existing

    requirement = DocumentRequirement(
        application_id=application.id,
        document_type=document_type,
        status=RequirementStatus.PENDING,
    )
    session.add(requirement)
    session.flush()

    record_event(
        session,
        event_type=EventType.REQUIREMENT_CREATED,
        requirement_id=requirement.id,
        application_id=application.id,
        application_reference=application.external_reference,
        payload={"document_type": document_type},
    )

    # Sales asking for the document is what starts the conversation
    # (`PRODUCT_SPEC.md` section 5).
    send_welcome(session, application, requirement.id)
    return requirement


def create_submission(
    session: Session,
    requirement: DocumentRequirement,
    *,
    storage_key: str,
    original_filename: str,
    file_size: int,
    declared_mime_type: str | None = None,
    detected_mime_type: str | None = None,
    sha256: str | None = None,
) -> Document:
    """Attach a new submission to a requirement.

    Any submission still in flight is superseded first, so a customer who re-sends
    before the previous attempt finished does not end up with two verdicts racing to
    set the requirement's status.
    """
    requirement.submission_count += 1
    document = Document(
        requirement_id=requirement.id,
        submission_index=requirement.submission_count,
        storage_key=storage_key,
        original_filename=original_filename,
        file_size=file_size,
        declared_mime_type=declared_mime_type,
        detected_mime_type=detected_mime_type,
        sha256=sha256,
    )
    session.add(document)
    session.flush()

    superseded = supersede_active_submissions(
        session, requirement, except_document_id=document.id
    )

    requirement.status = RequirementStatus.IN_PROGRESS
    session.flush()

    record_event(
        session,
        event_type=EventType.DOCUMENT_RECEIVED,
        document_id=document.id,
        requirement_id=requirement.id,
        application_id=requirement.application_id,
        payload={
            "submission_index": document.submission_index,
            "original_filename": original_filename,
            "file_size": file_size,
            "declared_mime_type": declared_mime_type,
        },
    )

    application = session.get(Application, requirement.application_id)
    if application is not None:
        record_inbound_document(session, application, document)
        if superseded:
            notify_superseded(session, application, superseded, document)
    return document
