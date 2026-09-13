"""Extraction stage of the pipeline.

Runs the extractor, normalises the result, persists both, and moves the document to
`EXTRACTED`. Validation (Phase 7) takes over from there.

Every attempt is persisted, successful or not. A failed extraction is part of how a
document ended up in front of a human, and an Operations card that cannot show what was
tried is not much use to the person holding it.
"""

from __future__ import annotations

import logging
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus, EventType, Outcome, TextLayer
from app.domain.extraction import ExtractedStatement, RawExtraction
from app.models.document import Document, DocumentExtraction
from app.providers.extractor.base import ExtractionError
from app.providers.extractor.router import extract_document
from app.services.audit import record_event
from app.services.document_state import transition
from app.services.normalization import parse_statement
from app.services.outcomes import record_outcome

logger = logging.getLogger("docverify.extraction")


def run_extraction(
    session: Session, document: Document, stream: BinaryIO
) -> ExtractedStatement | None:
    """Extract and normalise. Returns None when the document could not be read at all."""
    try:
        raw = extract_document(stream)
    except ExtractionError as exc:
        return _record_failure(session, document, str(exc))

    statement = parse_statement(raw)

    session.add(
        DocumentExtraction(
            document_id=document.id,
            provider=raw.provider,
            text_layer=_text_layer(raw),
            raw_result=raw.to_json(),
            normalized_data=statement.to_json(),
            field_confidence=statement.field_confidence,
            page_count=raw.document_page_count,
            duration_ms=raw.duration_ms,
        )
    )
    session.flush()

    record_event(
        session,
        event_type=EventType.EXTRACTION_COMPLETED,
        document_id=document.id,
        requirement_id=document.requirement_id,
        payload={
            "provider": raw.provider,
            "text_layer": raw.text_layer.value,
            "pages": len(raw.pages),
            "transactions": len(statement.transactions),
            "complete_transactions": len(statement.complete_transactions),
            "duration_ms": raw.duration_ms,
            "llm_assisted_fields": statement.llm_assisted_fields,
        },
    )

    transition(session, document, DocumentStatus.EXTRACTED)
    logger.info(
        "document %s extracted via %s: %s pages, %s transactions in %sms",
        document.id,
        raw.provider,
        len(raw.pages),
        len(statement.transactions),
        raw.duration_ms,
    )
    return statement


def _text_layer(raw: RawExtraction) -> str:
    return raw.text_layer.value if raw.pages else TextLayer.NONE.value


def _record_failure(session: Session, document: Document, error: str) -> None:
    """An extraction failure is our problem, not the customer's.

    There is no action they could usefully take -- the document may be perfectly good --
    so this goes to a human rather than becoming a FIX (ADR-014).
    """
    session.add(
        DocumentExtraction(
            document_id=document.id,
            provider="none",
            text_layer=TextLayer.NONE,
            error=error[:500],
        )
    )
    record_event(
        session,
        event_type=EventType.EXTRACTION_FAILED,
        document_id=document.id,
        requirement_id=document.requirement_id,
        payload={"error": error[:500]},
    )
    record_outcome(
        session,
        document,
        outcome=Outcome.REVIEW,
        reason_code="PROCESSING_ERROR",
        evidence={"stage": "extraction", "error": error[:200]},
    )
    logger.warning("document %s could not be extracted: %s", document.id, error)
    return None
