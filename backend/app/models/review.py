"""Operations review queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableUUID, new_uuid, utcnow
from app.domain.enums import ReviewStatus


class Review(Base):
    """A case the system declined to decide.

    Opening a review is not a failure of the product -- it is the product refusing to
    guess. The card carries the reason and the confidence breakdown so a human has
    something to act on rather than a bare percentage.
    """

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_review_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(
        PortableUUID, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    reason_code: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(default=None)

    status: Mapped[str] = mapped_column(String(16), default=ReviewStatus.OPEN)

    #: ACCEPT | REQUEST_NEW_DOCUMENT.
    reviewer_action: Mapped[str | None] = mapped_column(String(32), default=None)
    reviewer_note: Mapped[str | None] = mapped_column(String(1000), default=None)
    #: No user identity exists in the MVP (ADR-011); a real deployment keys this to an
    #: authenticated operator so decisions are attributable.
    reviewer_id: Mapped[str | None] = mapped_column(String(64), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
