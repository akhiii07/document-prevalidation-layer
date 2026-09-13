"""File-level checks (R-FILE-001 … R-FILE-004).

These run before extraction because they are cheap, certain, and universally applicable.
They also produce the clearest customer messages in the product, so it matters that they
stay distinct from one another:

    unsupported type  != structurally corrupt != unreadable content != invalid document

Collapsing those is the usual way document products end up telling a customer "there was
a problem with your file" -- which is true, useless, and gives them nothing to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO

from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError

from app.config.rules import ValidationRules


@dataclass
class FileCheckResult:
    """Outcome of the pre-extraction checks.

    `requires_password` is deliberately not a failure. An encrypted statement is a normal,
    expected thing for an Indian bank to have sent the customer
    (`RESEARCH_BANK_FORMATS.md` section 2) -- nothing is wrong with it.
    """

    ok: bool
    reason_code: str | None = None
    requires_password: bool = False
    detected_mime_type: str | None = None
    page_count: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.ok and not self.requires_password


def detect_mime_type(head: bytes, rules: ValidationRules) -> str | None:
    """Identify the file from its leading bytes.

    Never from the extension or the client-supplied `Content-Type`: both are
    attacker-controlled, and renaming a file is free.
    """
    for prefix in rules.file.magic_prefixes:
        if head.startswith(prefix.encode()):
            return "application/pdf"
    return None


def check_file(
    stream: BinaryIO,
    *,
    size: int,
    rules: ValidationRules,
    declared_mime_type: str | None = None,
) -> FileCheckResult:
    """Run every pre-extraction file check in causal order.

    The order is not cosmetic: each check makes the ones after it meaningful. There is no
    point asking whether a PDF is encrypted if the bytes are not a PDF.
    """
    if size == 0:
        return FileCheckResult(ok=False, reason_code="FILE_EMPTY", evidence={"size": 0})

    if size > rules.file.max_file_size_bytes:
        return FileCheckResult(
            ok=False,
            reason_code="FILE_TOO_LARGE",
            evidence={"size": size, "limit": rules.file.max_file_size_bytes},
        )

    stream.seek(0)
    head = stream.read(8)
    stream.seek(0)

    detected = detect_mime_type(head, rules)
    if detected not in rules.file.allowed_mime_types:
        return FileCheckResult(
            ok=False,
            reason_code="FILE_UNSUPPORTED_TYPE",
            evidence={
                "declared_mime_type": declared_mime_type,
                "detected_mime_type": detected,
                "allowed": list(rules.file.allowed_mime_types),
            },
        )

    return _check_pdf_structure(stream, detected, declared_mime_type)


def _check_pdf_structure(
    stream: BinaryIO, detected: str, declared_mime_type: str | None
) -> FileCheckResult:
    try:
        reader = PdfReader(stream)
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same verdict
        return FileCheckResult(
            ok=False,
            reason_code="FILE_CORRUPT",
            detected_mime_type=detected,
            evidence={"parser": "pypdf", "error": type(exc).__name__},
        )

    if reader.is_encrypted:
        # Stop here. Page access would raise, and there is nothing to diagnose: the file
        # is fine, we simply cannot read it yet.
        return FileCheckResult(
            ok=False,
            requires_password=True,
            reason_code="FILE_PASSWORD_REQUIRED",
            detected_mime_type=detected,
            evidence={"encrypted": True},
        )

    try:
        page_count = len(reader.pages)
        if page_count == 0:
            raise ValueError("no pages")
        # Touching a page forces the object graph to resolve. A file whose trailer parses
        # but whose page content is destroyed only fails here.
        _ = reader.pages[0]
    except (FileNotDecryptedError, DependencyError) as exc:
        return FileCheckResult(
            ok=False,
            requires_password=True,
            reason_code="FILE_PASSWORD_REQUIRED",
            detected_mime_type=detected,
            evidence={"encrypted": True, "error": type(exc).__name__},
        )
    except Exception as exc:  # noqa: BLE001
        return FileCheckResult(
            ok=False,
            reason_code="FILE_CORRUPT",
            detected_mime_type=detected,
            evidence={"parser": "pypdf", "stage": "pages", "error": type(exc).__name__},
        )

    return FileCheckResult(
        ok=True,
        detected_mime_type=detected,
        page_count=page_count,
        evidence={"declared_mime_type": declared_mime_type, "page_count": page_count},
    )
