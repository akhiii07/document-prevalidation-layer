"""Database-backed job queue (ADR-003)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableJSON, PortableUUID, TimestampMixin, new_uuid, utcnow
from app.domain.enums import JobStatus


class Job(Base, TimestampMixin):
    """A unit of background work.

    Living in the same database as the document state machine is the point: a job and
    the state transition it causes commit together, so the queue cannot drift out of
    step with the documents it is processing. That is the property Pub/Sub would have
    cost us, and at MVP volume it is worth more than throughput.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        # The claim query orders by (status, available_at).
        Index("ix_job_claim", "status", "available_at"),
        Index("ix_job_dedupe", "job_type", "dedupe_key"),
    )

    id: Mapped[str] = mapped_column(PortableUUID, primary_key=True, default=new_uuid)
    job_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)

    #: Enables backoff: a failed job becomes invisible until this time.
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    #: Set while a worker holds the job. A stale lock is reclaimable, so a worker that
    #: dies mid-job does not strand it forever.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    locked_by: Mapped[str | None] = mapped_column(String(64), default=None)

    #: Prevents duplicate work when the same document is enqueued twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(128), default=None)

    last_error: Mapped[str | None] = mapped_column(String(1000), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
