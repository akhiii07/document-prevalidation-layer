"""The conversation record."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableJSON, PortableUUID, new_uuid, utcnow
from app.domain.enums import MessageChannel, MessageDirection, MessageKind


class Message(Base):
    """One message to or from someone.

    Both the customer conversation and the Sales notification live here. Keeping them in
    one table gives a single answer to "what did the system tell anyone about this
    document, and when" -- which is what an auditor asks, and what the simulated WhatsApp
    thread renders.

    Like `document_events`, this carries **no foreign keys**: the conversation is part of
    the audit trail and must survive a retention purge of the document content it
    describes.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_message_application_created", "application_id", "created_at"),
        Index("ix_message_channel_created", "channel", "created_at"),
    )

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)

    application_id: Mapped[str] = mapped_column(PortableUUID, index=True)
    requirement_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)
    document_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)

    channel: Mapped[str] = mapped_column(String(16), default=MessageChannel.CUSTOMER)
    direction: Mapped[str] = mapped_column(String(16), default=MessageDirection.OUTBOUND)
    kind: Mapped[str] = mapped_column(String(32), default=MessageKind.INSTRUCTION)

    body: Mapped[str] = mapped_column(String(4000))
    #: Structured detail for the UI: filename, outcome, reason code. Never document
    #: content, never a password, never an unmasked account number.
    context: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, default=None)

    #: Which provider delivered it. "simulated" in the MVP.
    provider: Mapped[str] = mapped_column(String(32), default="simulated")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
