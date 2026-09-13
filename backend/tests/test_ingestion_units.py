"""Unit tests for the ingestion building blocks: storage, signed URLs, file checks,
password handling."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from app.config.rules import get_rules
from app.providers.storage import LocalFileStorage, StorageError, new_storage_key
from app.services.password import PasswordVault, WrongPasswordError, unlock
from app.services.signed_urls import InvalidTokenError, issue_token, verify_token
from app.validators.file_validator import check_file, detect_mime_type

# ------------------------------------------------------------------ storage


def test_storage_keys_are_opaque_and_unique() -> None:
    """Never derived from the uploaded filename: a caller-controlled name is a
    traversal and overwrite vector, and it leaks the customer's naming into storage."""
    keys = {new_storage_key() for _ in range(50)}
    assert len(keys) == 50
    for key in keys:
        assert key.endswith(".bin"), "the stored object carries no actionable extension"
        assert "statement" not in key


def test_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)
    key = new_storage_key()
    written = storage.write(key, iter([b"hello ", b"world"]))

    assert written == 11
    assert storage.read(key) == b"hello world"
    assert storage.exists(key)

    storage.delete(key)
    assert not storage.exists(key)


def test_delete_is_idempotent(tmp_path: Path) -> None:
    """Retention purges must be safe to re-run after a partial failure."""
    LocalFileStorage(tmp_path).delete("documents/2026/01/missing.bin")


@pytest.mark.parametrize(
    "key",
    ["../escape.bin", "documents/../../escape.bin", "/etc/passwd"],
)
def test_storage_refuses_keys_that_escape_the_root(tmp_path: Path, key: str) -> None:
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.write(key, iter([b"x"]))


# ------------------------------------------------------------------ signed URLs


def test_signed_token_round_trip() -> None:
    token, _ = issue_token("doc-1")
    verify_token("doc-1", token)  # does not raise


def test_token_is_bound_to_one_document() -> None:
    """Otherwise a single leaked token would unlock every document in the system."""
    token, _ = issue_token("doc-1")
    with pytest.raises(InvalidTokenError):
        verify_token("doc-2", token)


def test_tampered_token_is_rejected() -> None:
    token, _ = issue_token("doc-1")
    expiry, _, signature = token.partition(".")
    forged = f"{int(expiry) + 86400}.{signature}"

    with pytest.raises(InvalidTokenError):
        verify_token("doc-1", forged)


def test_expired_token_is_rejected() -> None:
    token, expires_at = issue_token("doc-1", ttl_seconds=-1)
    assert expires_at < time.time()
    with pytest.raises(InvalidTokenError, match="expired"):
        verify_token("doc-1", token)


@pytest.mark.parametrize("token", ["", "garbage", "notanumber.sig", "12345"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidTokenError):
        verify_token("doc-1", token)


# ------------------------------------------------------------------ file checks


def test_magic_bytes_beat_the_declared_type() -> None:
    rules = get_rules()
    assert detect_mime_type(b"%PDF-1.7", rules) == "application/pdf"
    assert detect_mime_type(b"GIF89a", rules) is None


def test_valid_statement_passes_file_checks(corpus_path) -> None:
    rules = get_rules()
    path = corpus_path("valid_hdfc")
    with path.open("rb") as fh:
        result = check_file(fh, size=path.stat().st_size, rules=rules)

    assert result.ok
    assert result.detected_mime_type == "application/pdf"
    assert result.page_count and result.page_count > 1


def test_text_file_renamed_to_pdf_is_caught(corpus_path) -> None:
    """The extension says PDF and the client would send `application/pdf`. Only the
    bytes tell the truth."""
    rules = get_rules()
    path = corpus_path("invalid_not_a_pdf")
    with path.open("rb") as fh:
        result = check_file(
            fh, size=path.stat().st_size, rules=rules, declared_mime_type="application/pdf"
        )

    assert result.reason_code == "FILE_UNSUPPORTED_TYPE"
    assert result.evidence["declared_mime_type"] == "application/pdf"
    assert result.evidence["detected_mime_type"] is None


def test_empty_file_is_caught(corpus_path) -> None:
    rules = get_rules()
    with corpus_path("invalid_empty").open("rb") as fh:
        result = check_file(fh, size=0, rules=rules)
    assert result.reason_code == "FILE_EMPTY"


def test_corrupt_file_is_distinguished_from_wrong_type(corpus_path) -> None:
    """Both are FIX, but they need different messages: 're-download from your bank'
    versus 'send a PDF'. Collapsing them is how document products give useless advice."""
    rules = get_rules()
    path = corpus_path("corrupt_truncated_axis")
    with path.open("rb") as fh:
        result = check_file(fh, size=path.stat().st_size, rules=rules)

    assert result.reason_code == "FILE_CORRUPT"
    assert result.detected_mime_type == "application/pdf", "the magic bytes were intact"


def test_oversize_file_is_caught_without_reading_it() -> None:
    rules = get_rules()
    result = check_file(
        io.BytesIO(b"%PDF-1.7"), size=rules.file.max_file_size_bytes + 1, rules=rules
    )
    assert result.reason_code == "FILE_TOO_LARGE"


def test_encrypted_pdf_is_a_gate_not_a_failure(corpus_path) -> None:
    """Encrypted statements are normal in India. Recording one as a validation failure
    would corrupt the failure-reason distribution."""
    rules = get_rules()
    path = corpus_path("password_protected_icici")
    with path.open("rb") as fh:
        result = check_file(fh, size=path.stat().st_size, rules=rules)

    assert result.requires_password is True
    assert result.failed is False
    assert result.reason_code == "FILE_PASSWORD_REQUIRED"


# ------------------------------------------------------------------ passwords


def test_vault_entries_are_single_use() -> None:
    vault = PasswordVault()
    vault.put("doc-1", "secret")
    assert vault.take("doc-1") == "secret"
    assert vault.take("doc-1") is None, "a password must not be reusable"


def test_vault_entries_expire() -> None:
    vault = PasswordVault(ttl_seconds=0)
    vault.put("doc-1", "secret")
    time.sleep(0.01)
    assert vault.take("doc-1") is None


def test_vault_repr_never_shows_contents() -> None:
    """A stray repr in a log line or traceback would defeat the whole class."""
    vault = PasswordVault()
    vault.put("doc-1", "SHAR1503")
    assert "SHAR1503" not in repr(vault)
    assert "doc-1" not in repr(vault)


def test_unlock_with_the_right_password(corpus_path, by_id) -> None:
    entry = by_id["password_protected_icici"]
    with corpus_path("password_protected_icici").open("rb") as fh:
        plaintext = unlock(fh, entry["password"])

    assert plaintext.getbuffer().nbytes > 0
    assert plaintext.read(5) == b"%PDF-"


def test_unlock_with_the_wrong_password(corpus_path) -> None:
    path = corpus_path("password_protected_icici")
    with path.open("rb") as fh, pytest.raises(WrongPasswordError):
        unlock(fh, "00000000")


def test_wrong_password_error_never_echoes_the_attempt(corpus_path) -> None:
    """The exception text travels into logs and error responses."""
    attempted = "hunter2-secret"
    with corpus_path("password_protected_icici").open("rb") as fh:
        try:
            unlock(fh, attempted)
        except WrongPasswordError as exc:
            assert attempted not in str(exc)
            assert exc.__cause__ is None, "a chained traceback could carry the attempt"
        else:
            pytest.fail("expected WrongPasswordError")
