"""Audit event recording.

Every event goes through `record_event`, and payloads are scrubbed on the way in. The
scrub is not a formality: the event log is the one table with a long retention period
(`RESEARCH_REGULATORY.md` section 2), so anything that leaks into it leaks for the
longest possible time.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.domain.enums import EventType
from app.models.event import DocumentEvent

logger = logging.getLogger("docverify.audit")

#: Keys that must never reach the audit log, matched case-insensitively as substrings.
#: `password` covers `pdf_password`, `password_attempt_value`, and anything else a
#: future caller invents.
FORBIDDEN_KEY_FRAGMENTS = ("password", "secret", "token", "api_key", "authorization")

#: A run of 9+ digits is an account number often enough that the audit log is the wrong
#: place to find out otherwise. Masked rather than dropped so the event stays readable.
_LONG_DIGITS = re.compile(r"\b\d{9,}\b")


def scrub(value: Any) -> Any:
    """Remove secrets and mask long digit runs, recursively."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if any(fragment in key.lower() for fragment in FORBIDDEN_KEY_FRAGMENTS):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = scrub(item)
        return cleaned
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return _LONG_DIGITS.sub(lambda m: "X" * (len(m.group()) - 4) + m.group()[-4:], value)
    return value


def record_event(
    session: Session,
    *,
    event_type: EventType,
    document_id: str | None = None,
    requirement_id: str | None = None,
    application_id: str | None = None,
    application_reference: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DocumentEvent:
    event = DocumentEvent(
        event_type=event_type,
        document_id=document_id,
        requirement_id=requirement_id,
        application_id=application_id,
        application_reference=application_reference,
        from_status=from_status,
        to_status=to_status,
        payload=scrub(payload) if payload else None,
    )
    session.add(event)
    session.flush()

    logger.info(
        "event=%s document=%s %s->%s",
        event_type.value,
        document_id,
        from_status or "-",
        to_status or "-",
    )
    return event
