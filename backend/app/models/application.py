"""Applications and the document obligations attached to them."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PortableUUID, TimestampMixin, new_uuid
from app.domain.enums import ApplicationStatus, RequirementStatus


class Application(Base, TimestampMixin):
    """A loan application. Seeded in the MVP rather than integrated with a real LOS."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    external_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    borrower_name: Mapped[str] = mapped_column(String(200))
    #: The legal/registered business name. Identity checks compare the statement's
    #: account holder against this (R-IDN-001).
    business_name: Mapped[str] = mapped_column(String(200))
    phone_number: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(32), default=ApplicationStatus.OPEN)

    requirements: Mapped[list[DocumentRequirement]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class DocumentRequirement(Base, TimestampMixin):
    """An obligation: "this application owes a 6-month current-account statement".

    This exists so that First-Time Document Clearance Rate is computable (ADR-006).
    Submissions are `documents` rows pointing here; the requirement is the denominator.
    Without it, every re-upload would inflate the denominator and the headline metric
    would improve as rework increased -- exactly backwards.
    """

    __tablename__ = "document_requirements"
    __table_args__ = (
        # One outstanding requirement per document type per application.
        UniqueConstraint("application_id", "document_type", name="uq_requirement_per_type"),
        Index("ix_requirement_status", "status"),
    )

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    application_id: Mapped[str] = mapped_column(
        PortableUUID, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(64), default="bank_statement")
    status: Mapped[str] = mapped_column(String(32), default=RequirementStatus.PENDING)

    #: Number of submissions received. The metric's denominator is requirements, but
    #: this is what distinguishes "cleared" from "cleared first time".
    submission_count: Mapped[int] = mapped_column(default=0)

    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cleared_by_document_id: Mapped[str | None] = mapped_column(PortableUUID, default=None)

    #: Denormalised at clearance time rather than derived on read. The metric must
    #: remain computable after documents are purged under the retention policy
    #: (`RESEARCH_REGULATORY.md` section 2), and a purge would take the submission
    #: index with it.
    first_time_cleared: Mapped[bool | None] = mapped_column(default=None)

    application: Mapped[Application] = relationship(back_populates="requirements")
    documents: Mapped[list[Document]] = relationship(  # noqa: F821
        back_populates="requirement",
        cascade="all, delete-orphan",
        order_by="Document.submission_index",
    )
