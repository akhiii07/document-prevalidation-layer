"""Purging a document's content while keeping the record that it existed.

The schema was built for this (`app/domain/enums.py`, `providers/storage.py`): events
outlive the documents they describe, so a purge can remove content without erasing the
history of how an application was decided. This module is the path that actually uses it.

The distinction it draws is the whole point:

* **Content** -- the stored file, the extracted transactions, the account holder's name,
  the filename they chose -- is removable, and should be removed the moment it is no
  longer needed.
* **The record** -- that a submission arrived at a time, moved through states, and
  produced a verdict -- is not. Deleting it would make the audit trail lie by omission,
  and a lending decision that cannot be explained afterwards is not usable.

Written after a real bank statement was uploaded into the prototype during testing,
against the project's own synthetic-documents-only constraint (`PRODUCT_SPEC.md` §12).
A constraint with no means of putting a mistake right is a wish, not a control.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import EventType
from app.models.document import Document, DocumentExtraction
from app.models.message import Message
from app.providers.storage import StorageProvider, get_storage
from app.services.audit import record_event

logger = logging.getLogger("docverify.retention")

#: What a purged document's filename becomes. Not blank: a reader of the audit trail
#: should be able to tell "this was removed" from "this was never recorded".
PURGED_PLACEHOLDER = "[purged]"


def purge_document(
    session: Session,
    document: Document,
    *,
    reason: str,
    storage: StorageProvider | None = None,
) -> dict[str, int]:
    """Remove a document's content. The submission row and its events survive.

    Idempotent, because a retention job that fails halfway must be safe to re-run.
    """
    storage = storage or get_storage()
    removed = {"objects": 0, "extractions": 0, "messages": 0}

    # Idempotency comes from asking storage what is actually there, not from a flag we
    # set: a purge that fails after deleting the object but before committing must not
    # then report "nothing to do" on the re-run. The key itself is kept -- it is opaque,
    # it is unique, and it now points at nothing.
    if document.storage_key and storage.exists(document.storage_key):
        storage.delete(document.storage_key)
        removed["objects"] = 1

    extractions = session.scalars(
        select(DocumentExtraction).where(DocumentExtraction.document_id == document.id)
    ).all()
    for extraction in extractions:
        # The normalised payload holds transactions, dates and the account holder's
        # name. It is the densest concentration of customer data in the system.
        extraction.raw_result = None
        extraction.normalized_data = None
        extraction.field_confidence = None
        removed["extractions"] += 1

    # Messages quote the filename back to the customer, and the inbound bubble *is* the
    # filename. Scrubbed in place rather than deleted: the conversation is evidence of
    # what the customer was told, which is precisely what an audit needs to see.
    messages = session.scalars(
        select(Message).where(Message.document_id == document.id)
    ).all()
    original = document.original_filename
    for message in messages:
        if original and original in message.body:
            message.body = message.body.replace(original, PURGED_PLACEHOLDER)
        if message.context and "filename" in message.context:
            message.context = {**message.context, "filename": PURGED_PLACEHOLDER}
        removed["messages"] += 1

    document.original_filename = PURGED_PLACEHOLDER
    document.sha256 = None
    session.flush()

    record_event(
        session,
        event_type=EventType.DOCUMENT_PURGED,
        document_id=document.id,
        requirement_id=document.requirement_id,
        payload={"reason": reason, **removed},
    )
    logger.info("purged document %s: %s (%s)", document.id, removed, reason)
    return removed
