"""Declarative base and portable column types.

The application runs on SQLite for local development and PostgreSQL for parity and
deployment (ADR-012). To keep those two honest, every model uses the portable types
defined here rather than backend-specific ones. Nothing in the codebase may import
`postgresql.JSONB` or `postgresql.UUID` directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON as SAJSON
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSON everywhere; JSONB where the backend supports it.
PortableJSON = SAJSON().with_variant(JSONB, "postgresql")

# UUIDs are stored as 36-char strings so identifiers are byte-identical across
# backends, in logs, and in URLs.
PortableUUID = String(36)


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
