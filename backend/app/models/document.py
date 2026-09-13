"""Document submissions and their extraction results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PortableJSON, PortableUUID, TimestampMixin, new_uuid, utcnow
from app.domain.enums import DocumentStatus, TextLayer


class Document(Base, TimestampMixin):
    """One *submission* against a requirement.

    A re-upload is a new row, not a mutation of this one: the correction loop needs the
    history, and the audit trail must show what was actually received each time.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("requirement_id", "submission_index", name="uq_submission_index"),
        Index("ix_document_status", "status"),
    )

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    requirement_id: Mapped[str] = mapped_column(
        PortableUUID, ForeignKey("document_requirements.id", ondelete="CASCADE"), index=True
    )

    #: 1-based. `submission_index == 1` at clearance is what "first time" means.
    submission_index: Mapped[int] = mapped_column()

    #: Opaque generated key. Never derived from user input: a caller-controlled filename
    #: is a path-traversal and overwrite vector (`PRODUCT_SPEC.md` section 12).
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    #: Kept only to quote back to the customer. Never used to build a path.
    original_filename: Mapped[str] = mapped_column(String(255))

    #: What the client claimed, retained for audit -- and deliberately distinct from
    #: the type we detected, because the client's claim is attacker-controlled.
    declared_mime_type: Mapped[str | None] = mapped_column(String(128), default=None)
    detected_mime_type: Mapped[str | None] = mapped_column(String(128), default=None)

    file_size: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, default=None)

    is_encrypted: Mapped[bool] = mapped_column(default=False)
    #: Rate-limits password guessing (R-FILE-004). The password itself is never stored,
    #: logged, or hashed -- only the count of attempts.
    password_attempts: Mapped[int] = mapped_column(default=0)

    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.RECEIVED)

    requirement: Mapped[DocumentRequirement] = relationship(  # noqa: F821
        back_populates="documents"
    )
    extractions: Mapped[list[DocumentExtraction]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    validation_results: Mapped[list[ValidationResult]] = relationship(  # noqa: F821
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentExtraction(Base):
    """The output of one extraction attempt.

    Attempts are kept rather than overwritten: when a password unlocks a document, or a
    retry follows a transient failure, the earlier attempt is part of how the final
    verdict came about.
    """

    __tablename__ = "document_extractions"

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(
        PortableUUID, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    provider: Mapped[str] = mapped_column(String(64))
    provider_version: Mapped[str | None] = mapped_column(String(64), default=None)
    text_layer: Mapped[str] = mapped_column(String(16), default=TextLayer.NONE)

    #: Provider-shaped output, kept for debugging and for re-normalisation without
    #: re-reading the document.
    raw_result: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, default=None)
    #: The canonical schema every bank layout is mapped into.
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, default=None)
    #: Per-field confidence. Feeds the weighted field score (VALIDATION_RULES 8.1).
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, default=None)

    page_count: Mapped[int | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(String(500), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="extractions")
