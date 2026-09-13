"""Append-only audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableJSON, PortableUUID, new_uuid, utcnow


class DocumentEvent(Base):
    """One immutable fact about something that happened.

    Two design points that are requirements rather than preferences:

    1. **No foreign keys.** Events must outlive the rows they describe. Document content
       is purgeable under a retention policy, but the audit trail is retained
       (`RESEARCH_REGULATORY.md` section 2) -- a cascade would destroy exactly the record
       that has to survive. Identifiers are stored as plain values.

    2. **Never updated.** There is no `updated_at` and nothing rewrites a row. A
       correction is a new event.

    Every metric in `PRODUCT_SPEC.md` section 24 is computed from this table.
    """

    __tablename__ = "document_events"
    __table_args__ = (
        Index("ix_event_document_created", "document_id", "created_at"),
        Index("ix_event_type_created", "event_type", "created_at"),
        Index("ix_event_requirement", "requirement_id"),
    )

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)

    document_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)
    requirement_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)
    application_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)
    #: Denormalised so an event stays human-readable after the application row is gone.
    application_reference: Mapped[str | None] = mapped_column(String(64), default=None)

    event_type: Mapped[str] = mapped_column(String(48), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), default=None)
    to_status: Mapped[str | None] = mapped_column(String(32), default=None)

    #: Free-form detail. Must never contain a password, a full account number, or
    #: document content (`PRODUCT_SPEC.md` section 12).
    payload: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
