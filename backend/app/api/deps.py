"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.config.settings import get_settings


def require_ops_secret(x_ops_secret: str | None = Header(default=None)) -> None:
    """Gate Operations-only endpoints.

    Prototype-grade by design (ADR-011): a shared secret, not user identity. It exists
    because the alternative -- leaving document download and the review queue openly
    enumerable -- would make the security section of the spec decorative. A real
    deployment needs per-operator identity so review decisions are attributable.
    """
    expected = get_settings().ops_shared_secret.get_secret_value()
    if not x_ops_secret or not hmac.compare_digest(x_ops_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="operations credentials required",
            headers={"WWW-Authenticate": "X-Ops-Secret"},
        )
