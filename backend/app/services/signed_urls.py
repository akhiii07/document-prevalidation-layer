"""Short-lived signed download tokens.

Documents are never publicly addressable. A download requires a token that is bound to
one document id, expires quickly, and is verified with a constant-time comparison
(`PRODUCT_SPEC.md` section 12).

The token carries its own expiry, so verification needs no server-side state -- which is
also why the expiry must be *inside* the signed payload. Signing the id alone and
carrying the expiry alongside would let anyone extend their own access.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.config.settings import get_settings


class InvalidTokenError(Exception):
    """Raised for a malformed, tampered, or expired token."""


def _sign(payload: str, key: str) -> str:
    digest = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_token(document_id: str, *, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Return `(token, expires_at_epoch)` for one document."""
    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.signed_url_ttl_seconds
    expires_at = int(time.time()) + ttl
    payload = f"{document_id}:{expires_at}"
    signature = _sign(payload, settings.url_signing_key.get_secret_value())
    return f"{expires_at}.{signature}", expires_at


def verify_token(document_id: str, token: str) -> None:
    """Raise `InvalidTokenError` unless the token is valid for this document, right now."""
    settings = get_settings()

    expires_raw, _, signature = token.partition(".")
    if not signature:
        raise InvalidTokenError("malformed token")

    try:
        expires_at = int(expires_raw)
    except ValueError as exc:
        raise InvalidTokenError("malformed expiry") from exc

    expected = _sign(f"{document_id}:{expires_at}", settings.url_signing_key.get_secret_value())
    # Constant time: a short-circuiting comparison leaks the signature one byte at a time.
    if not hmac.compare_digest(expected, signature):
        raise InvalidTokenError("signature mismatch")

    # Checked after the signature so an attacker cannot distinguish "expired" from
    # "forged" by timing or by the error they get back.
    if expires_at < int(time.time()):
        raise InvalidTokenError("token expired")
