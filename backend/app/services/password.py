"""Password-protected PDF handling.

The binding rules (`RESEARCH_REGULATORY.md` section 3) are:

* the password is **never logged, never persisted, and not hashed** -- a hash of a
  DOB- or customer-ID-derived password is trivially reversible and is itself personal
  data, so storing one creates liability with no benefit;
* the **decrypted document is never written to disk**; only the original encrypted file
  is stored;
* attempts are rate-limited.

That creates a real problem, because processing is asynchronous: the password arrives in
an HTTP request, but the worker needs it moments later to read the file. `PasswordVault`
is the answer -- a process-local, single-use, TTL-bounded holding area. See ADR-015 for
the alternatives considered.
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO

from pypdf import PdfReader, PdfWriter

#: Deliberately short. The vault exists to bridge one HTTP request and the worker run it
#: triggers, which is a matter of seconds. Anything longer is a cache, not a handoff.
DEFAULT_TTL_SECONDS = 300


class WrongPasswordError(Exception):
    """The supplied password did not decrypt the document."""


@dataclass(frozen=True)
class _Entry:
    secret: str
    expires_at: float


class PasswordVault:
    """Process-local, single-use, expiring store for PDF passwords.

    Nothing here is serialised, logged, or written anywhere. `take` removes the entry, so
    a password cannot be reused after the processing run it was supplied for.

    **Known limit:** being process-local, this only works because the worker runs
    in-process with the API (ADR-003/ADR-004). Moving to out-of-process workers would
    require re-prompting the customer instead -- not a shared cache, which would mean
    passwords crossing a network and living in another system's memory.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def put(self, document_id: str, password: str) -> None:
        with self._lock:
            self._purge_expired()
            self._entries[document_id] = _Entry(password, time.monotonic() + self._ttl)

    def take(self, document_id: str) -> str | None:
        """Remove and return the password, or None if absent or expired."""
        with self._lock:
            self._purge_expired()
            entry = self._entries.pop(document_id, None)
            return entry.secret if entry else None

    def discard(self, document_id: str) -> None:
        with self._lock:
            self._entries.pop(document_id, None)

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
            del self._entries[key]

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._entries)

    def __repr__(self) -> str:
        # Never render contents. A stray repr in a log or traceback would defeat the
        # entire point of this class.
        return f"<PasswordVault entries={len(self)}>"


_vault = PasswordVault()


def get_password_vault() -> PasswordVault:
    return _vault


def unlock(stream: BinaryIO, password: str) -> io.BytesIO:
    """Decrypt in memory and return the plaintext PDF bytes.

    The result is a `BytesIO` and is never handed to anything that writes to disk. The
    caller is expected to consume it within one processing run and let it be collected.
    """
    stream.seek(0)
    reader = PdfReader(stream)
    if not reader.is_encrypted:
        stream.seek(0)
        return io.BytesIO(stream.read())

    try:
        accepted = reader.decrypt(password)
    except Exception:  # noqa: BLE001 - any decryption failure is one verdict
        # Deliberately not chained. A chained traceback can carry the library's internal
        # context, and the last thing that should ever reach a log is the attempted
        # password.
        raise WrongPasswordError("could not decrypt with the supplied password") from None

    if not accepted:
        raise WrongPasswordError("could not decrypt with the supplied password")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata(reader.metadata or {})

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def is_encrypted(stream: BinaryIO) -> bool:
    stream.seek(0)
    try:
        return PdfReader(stream).is_encrypted
    except Exception:  # noqa: BLE001
        return False
    finally:
        stream.seek(0)
