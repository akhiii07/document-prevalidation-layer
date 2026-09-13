"""Receiving a file and starting the pipeline."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.config.rules import get_rules
from app.domain.enums import JobType
from app.models.application import Application, DocumentRequirement
from app.models.document import Document
from app.providers.queue import JobQueue, get_queue
from app.providers.storage import StorageProvider, get_storage, new_storage_key
from app.services.intake import DEFAULT_DOCUMENT_TYPE, create_submission, request_document

CHUNK_SIZE = 64 * 1024


def _capped_chunks(source: BinaryIO, limit: int, state: dict) -> Iterator[bytes]:
    """Yield chunks, hashing as we go, and stop one byte past the size limit.

    Reading the whole upload into memory before checking its size would make the size
    limit useless as a denial-of-service control -- the damage is done by the time you
    look. Stopping at `limit + 1` is enough to *know* the file is too large without
    holding an unbounded amount of it.
    """
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        if total + len(chunk) > limit:
            chunk = chunk[: limit - total]
            if chunk:
                digest.update(chunk)
                total += len(chunk)
                yield chunk
            state["exceeded_limit"] = True
            break
        digest.update(chunk)
        total += len(chunk)
        yield chunk

    state["size"] = total
    state["sha256"] = digest.hexdigest()


def store_upload(
    source: BinaryIO,
    *,
    storage: StorageProvider | None = None,
) -> tuple[str, int, str, bool]:
    """Stream an upload into storage. Returns (key, size, sha256, exceeded_limit)."""
    storage = storage or get_storage()
    rules = get_rules()
    key = new_storage_key()
    state: dict = {"exceeded_limit": False}

    # +1 so a file exactly at the limit is accepted and one byte over is detected.
    storage.write(key, _capped_chunks(source, rules.file.max_file_size_bytes + 1, state))
    return key, int(state["size"]), str(state["sha256"]), bool(state["exceeded_limit"])


def ingest(
    session: Session,
    application: Application,
    *,
    source: BinaryIO,
    original_filename: str,
    declared_mime_type: str | None = None,
    document_type: str = DEFAULT_DOCUMENT_TYPE,
    storage: StorageProvider | None = None,
    queue: JobQueue | None = None,
) -> tuple[Document, DocumentRequirement]:
    """Accept a submission and queue it for processing.

    Checks run in the worker rather than here. Doing them inline would be faster to
    report, but it would mean two different paths reaching a verdict -- and the one that
    ran inside a request would bypass the retry and dead-letter guarantees that make a
    verdict reliable.
    """
    queue = queue or get_queue()
    requirement = request_document(session, application, document_type=document_type)

    key, size, sha256, _exceeded = store_upload(source, storage=storage)

    document = create_submission(
        session,
        requirement,
        storage_key=key,
        original_filename=original_filename,
        file_size=size,
        declared_mime_type=declared_mime_type,
        sha256=sha256,
    )

    queue.enqueue(
        session,
        JobType.PROCESS_DOCUMENT,
        {"document_id": document.id},
        # One processing run per submission. A customer double-tapping upload creates a
        # second submission, not a second run of the first.
        dedupe_key=f"document:{document.id}",
    )
    return document, requirement
