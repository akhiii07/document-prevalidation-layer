"""Purging content without erasing the record that it existed.

The asymmetry is the point, and it is the thing worth testing: content goes, history
stays. A purge that also removed the audit trail would leave a lending decision that
cannot be explained afterwards, which is a different kind of failure from the one it was
trying to fix.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus, EventType, MessageKind
from app.models.application import DocumentRequirement
from app.models.document import Document, DocumentExtraction
from app.models.event import DocumentEvent
from app.models.message import Message
from app.providers.storage import get_storage, new_storage_key
from app.services.document_state import transition
from app.services.intake import create_submission
from app.services.retention import PURGED_PLACEHOLDER, purge_document


def _submitted(session: Session, requirement: DocumentRequirement) -> Document:
    storage = get_storage()
    key = new_storage_key()
    storage.write(key, iter([b"%PDF-1.4 pretend statement"]))
    document = create_submission(
        session,
        requirement,
        storage_key=key,
        original_filename="my-real-statement.pdf",
        file_size=25,
        declared_mime_type="application/pdf",
        sha256="a" * 64,
    )
    session.add(
        DocumentExtraction(
            document_id=document.id,
            provider="test",
            text_layer="NATIVE",
            raw_result={"pages": 7},
            normalized_data={"account_holder_name": "A REAL PERSON"},
            field_confidence={"account_holder_name": 1.0},
        )
    )
    transition(session, document, DocumentStatus.INGESTING)
    session.flush()
    return document


def test_purge_removes_the_content(session: Session, requirement: DocumentRequirement) -> None:
    document = _submitted(session, requirement)
    key = document.storage_key

    purge_document(session, document, reason="uploaded in error")

    extraction = session.scalars(
        select(DocumentExtraction).where(DocumentExtraction.document_id == document.id)
    ).one()
    assert extraction.normalized_data is None, "the transactions are the densest PII here"
    assert extraction.raw_result is None
    assert document.original_filename == PURGED_PLACEHOLDER
    assert document.sha256 is None
    assert not get_storage().exists(key)


def test_purge_keeps_the_record_that_the_submission_happened(
    session: Session, requirement: DocumentRequirement
) -> None:
    document = _submitted(session, requirement)
    before = len(
        session.scalars(select(DocumentEvent).where(DocumentEvent.document_id == document.id)).all()
    )

    purge_document(session, document, reason="uploaded in error")

    events = session.scalars(
        select(DocumentEvent).where(DocumentEvent.document_id == document.id)
    ).all()
    assert len(events) == before + 1, "history must survive the content"
    assert any(e.event_type == EventType.DOCUMENT_PURGED for e in events)
    assert session.get(Document, document.id) is not None
    assert requirement.submission_count == 1, "the metric must not be rewritten by a purge"


def test_purge_scrubs_the_filename_from_the_conversation(
    session: Session, requirement: DocumentRequirement
) -> None:
    """The inbound bubble *is* the filename, and a filename is the customer's own words."""
    document = _submitted(session, requirement)

    purge_document(session, document, reason="uploaded in error")

    messages = session.scalars(
        select(Message).where(Message.document_id == document.id)
    ).all()
    assert messages
    assert all("my-real-statement.pdf" not in message.body for message in messages)
    assert any(message.kind == MessageKind.DOCUMENT_RECEIVED for message in messages)
    assert all(
        (message.context or {}).get("filename") in (None, PURGED_PLACEHOLDER)
        for message in messages
    )


def test_purge_is_idempotent(session: Session, requirement: DocumentRequirement) -> None:
    """A retention job that fails halfway must be safe to re-run."""
    document = _submitted(session, requirement)
    purge_document(session, document, reason="first")
    second = purge_document(session, document, reason="second")
    assert second["objects"] == 0
