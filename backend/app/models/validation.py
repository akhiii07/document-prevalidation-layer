"""Validation results.

Every verdict stores the evidence it was built from, not just the answer. An outcome
that cannot be explained after the fact is not usable in a lending workflow, and the
Operations card is rendered from these fields rather than re-deriving anything.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PortableJSON, PortableUUID, new_uuid, utcnow


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (Index("ix_validation_outcome", "outcome"),)

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(
        PortableUUID, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    #: PASS | FIX | REVIEW.
    outcome: Mapped[str] = mapped_column(String(16))
    #: The single issue shown to the customer. Validated against the rule config's
    #: catalogue rather than an enum, so `validation_rules.yaml` stays the one source.
    primary_reason_code: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    secondary_reason_codes: Mapped[list[str] | None] = mapped_column(PortableJSON, default=None)

    #: Composite confidence and its two components, stored separately because
    #: Operations needs to see *which* signal was weak, not just a number (ADR-009).
    composite_confidence: Mapped[float | None] = mapped_column(default=None)
    field_score: Mapped[float | None] = mapped_column(default=None)
    rule_coverage: Mapped[float | None] = mapped_column(default=None)

    #: One entry per rule: code, status (PASS/FAIL/NOT_EVALUATED/SIGNAL), and the actual
    #: values compared. A rule that cannot produce evidence is not fit for purpose.
    rule_results: Mapped[list[dict[str, Any]] | None] = mapped_column(PortableJSON, default=None)

    #: The message actually sent. Stored so the audit trail shows what the customer was
    #: told, which may differ in wording from what the current templates would produce.
    customer_message: Mapped[str | None] = mapped_column(String(2000), default=None)

    #: Which rule config produced this verdict, so old results stay interpretable after
    #: thresholds are tuned.
    rules_version: Mapped[int | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="validation_results")  # noqa: F821
