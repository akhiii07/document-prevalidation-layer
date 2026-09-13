"""End-to-end ingestion through the HTTP API and the worker.

These drive the real upload endpoint, the real queue, and the real worker against real
corpus PDFs. Acceptance scenarios 3 (password-protected) and 5 (corrupt / unusable) from
`PRODUCT_SPEC.md` section 10 are proven here at the ingestion level. The full
scenario sweep lives in `test_scenarios.py`.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.domain.enums import DocumentStatus, EventType, JobType
from app.models.document import Document
from app.models.event import DocumentEvent
from app.models.validation import ValidationResult
from app.providers.storage import get_storage
from app.services.pipeline import process_document
from app.workers.document_processor import Worker

OPS_HEADERS = {"X-Ops-Secret": get_settings().ops_shared_secret.get_secret_value()}


def drain(limit: int = 20) -> int:
    """Run queued jobs to completion, as the background worker would."""
    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: process_document})
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1
    return processed


@pytest.fixture
def app_id(client: TestClient, session: Session) -> str:
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-55021",
            "borrower_name": "Rajesh Kumar Sharma",
            "business_name": "Sharma Metal Works Private Limited",
            "phone_number": "+919820000000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client: TestClient, app_id: str, path: Path, *, content_type: str = "application/pdf"):
    with path.open("rb") as fh:
        return client.post(
            f"/applications/{app_id}/documents",
            files={"file": (path.name, fh, content_type)},
        )


# ------------------------------------------------------------------ application


def test_creating_an_application_also_creates_the_requirement(
    client: TestClient, session: Session
) -> None:
    """Sales asking for the document is what starts the clock, not the file arriving."""
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-90001",
            "borrower_name": "A Borrower",
            "business_name": "A Business Pvt Ltd",
            "phone_number": "+919000000000",
        },
    )
    body = response.json()
    assert response.status_code == 201
    assert len(body["requirements"]) == 1
    assert body["requirements"][0]["document_type"] == "bank_statement"
    assert body["requirements"][0]["status"] == "PENDING"


def test_duplicate_application_reference_is_rejected(client: TestClient, app_id: str) -> None:
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-55021",
            "borrower_name": "Someone Else",
            "business_name": "Another Business",
            "phone_number": "+919000000001",
        },
    )
    assert response.status_code == 409


# ------------------------------------------------------------------ scenario 1 (partial)


def test_valid_statement_passes_file_checks_and_awaits_extraction(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    response = upload(client, app_id, corpus_path("valid_hdfc"))
    assert response.status_code == 202
    document_id = response.json()["id"]
    assert response.json()["status"] == DocumentStatus.RECEIVED

    assert drain() == 1

    status = client.get(f"/documents/{document_id}/status").json()
    # File checks, extraction and validation all run in one pass.
    assert status["status"] == DocumentStatus.PASSED
    assert status["outcome"] == "PASS"


def test_upload_response_never_leaks_the_storage_key(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """The storage location tells a caller nothing useful and something about us."""
    response = upload(client, app_id, corpus_path("valid_hdfc"))
    assert "storage_key" not in response.text
    assert "documents/20" not in response.text


# ------------------------------------------------------------------ scenario 5


@pytest.mark.scenario
def test_corrupt_document_produces_a_fix_with_a_specific_action(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    document_id = upload(client, app_id, corpus_path("corrupt_truncated_axis")).json()["id"]
    drain()

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["status"] == DocumentStatus.NEEDS_FIX
    assert status["outcome"] == "FIX"
    assert status["reason_code"] == "FILE_CORRUPT"

    message = status["customer_message"]
    assert "damaged or incomplete" in message
    assert "download the statement from your bank again" in message


@pytest.mark.scenario
def test_non_pdf_is_rejected_on_magic_bytes(client: TestClient, app_id: str, corpus_path) -> None:
    """Uploaded with a PDF filename and a PDF content type. Only the bytes disagree."""
    document_id = upload(client, app_id, corpus_path("invalid_not_a_pdf")).json()["id"]
    drain()

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["reason_code"] == "FILE_UNSUPPORTED_TYPE"
    assert "PDF" in status["customer_message"]


@pytest.mark.scenario
def test_empty_upload_is_rejected(client: TestClient, app_id: str, corpus_path) -> None:
    document_id = upload(client, app_id, corpus_path("invalid_empty")).json()["id"]
    drain()

    assert client.get(f"/documents/{document_id}/status").json()["reason_code"] == "FILE_EMPTY"


def test_customer_messages_never_expose_internals(
    client: TestClient, app_id: str, corpus_path
) -> None:
    """No reason codes, confidence numbers or stack traces in customer-facing text
    (`PRODUCT_SPEC.md` section 9)."""
    document_id = upload(client, app_id, corpus_path("corrupt_truncated_axis")).json()["id"]
    drain()

    message = client.get(f"/documents/{document_id}/status").json()["customer_message"]
    for leak in ("FILE_CORRUPT", "R-FILE", "Traceback", "pypdf", "confidence"):
        assert leak not in message


# ------------------------------------------------------------------ scenario 3


@pytest.mark.scenario
def test_password_protected_pdf_unlocks_and_continues(
    client: TestClient, session: Session, app_id: str, corpus_path, by_id
) -> None:
    """Scenario 3: detect encryption -> prompt -> decrypt in memory -> continue."""
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["status"] == DocumentStatus.AWAITING_PASSWORD
    assert status["awaiting_password"] is True
    assert status["outcome"] is None, "an encrypted file is not a validation failure"

    accepted = client.post(
        f"/documents/{document_id}/password",
        json={"password": by_id["password_protected_icici"]["password"]},
    )
    assert accepted.status_code == 202
    drain()

    assert client.get(f"/documents/{document_id}/status").json()["status"] == (
        DocumentStatus.PASSED
    )


def test_wrong_password_is_rejected_and_counted(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    client.post(f"/documents/{document_id}/password", json={"password": "00000000"})
    drain()

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["status"] == DocumentStatus.AWAITING_PASSWORD, "still recoverable"
    assert status["password_attempts_remaining"] == 4


def test_password_attempts_are_rate_limited(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """Without a cap, a DOB-derived password is brute-forceable in a few thousand tries."""
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    for _ in range(5):
        client.post(f"/documents/{document_id}/password", json={"password": "00000000"})
        drain()

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["status"] == DocumentStatus.NEEDS_FIX
    assert status["reason_code"] == "FILE_PASSWORD_INCORRECT"

    refused = client.post(f"/documents/{document_id}/password", json={"password": "00000000"})
    assert refused.status_code in (409, 429)


def test_password_endpoint_rejects_documents_not_awaiting_one(
    client: TestClient, app_id: str, corpus_path
) -> None:
    document_id = upload(client, app_id, corpus_path("valid_hdfc")).json()["id"]
    drain()

    response = client.post(f"/documents/{document_id}/password", json={"password": "x"})
    assert response.status_code == 409


# ------------------------------------------------------------------ password secrecy


def test_password_never_reaches_the_database(
    client: TestClient, session: Session, app_id: str, corpus_path, by_id
) -> None:
    """The binding rule: never logged, never persisted, not even hashed
    (`RESEARCH_REGULATORY.md` section 3)."""
    secret = by_id["password_protected_icici"]["password"]
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()
    client.post(f"/documents/{document_id}/password", json={"password": secret})
    drain()

    session.expire_all()
    events = session.scalars(select(DocumentEvent)).all()
    documents = session.scalars(select(Document)).all()
    results = session.scalars(select(ValidationResult)).all()

    haystack = " ".join(
        [str(e.payload) for e in events]
        + [f"{d.original_filename}{d.storage_key}{d.detected_mime_type}" for d in documents]
        + [str(r.customer_message) for r in results]
    )
    assert secret not in haystack

    # And the document row carries only the attempt count, never the value.
    document = session.get(Document, document_id)
    assert document is not None
    password_columns = [
        c.name
        for c in Document.__table__.columns
        if "password" in c.name and c.name != "password_attempts"
    ]
    assert password_columns == [], "the schema has nowhere to put a password, by design"


def test_password_never_reaches_the_logs(
    client: TestClient, app_id: str, corpus_path, by_id, caplog: pytest.LogCaptureFixture
) -> None:
    secret = by_id["password_protected_icici"]["password"]
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    with caplog.at_level(logging.DEBUG):
        client.post(f"/documents/{document_id}/password", json={"password": secret})
        drain()

    assert secret not in caplog.text


def test_wrong_password_attempt_never_reaches_the_logs(
    client: TestClient, app_id: str, corpus_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed attempt is the likeliest thing to end up in an exception trace."""
    attempted = "WRONGPASS1234"
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    with caplog.at_level(logging.DEBUG):
        client.post(f"/documents/{document_id}/password", json={"password": attempted})
        drain()

    assert attempted not in caplog.text


def test_decrypted_content_is_never_written_to_storage(
    client: TestClient, session: Session, app_id: str, corpus_path, by_id
) -> None:
    """Only the encrypted original is retained. A decrypted copy on disk would be a
    plaintext financial document sitting outside the protection the bank applied."""
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()
    client.post(
        f"/documents/{document_id}/password",
        json={"password": by_id["password_protected_icici"]["password"]},
    )
    drain()

    session.expire_all()
    document = session.get(Document, document_id)
    assert document is not None

    # Scanning the bytes for a known string would prove nothing: PDF text lives in
    # compressed streams, so the plain string is absent even from a decrypted file.
    # The real question is whether the stored object still *requires* a password.
    stored = get_storage().read(document.storage_key)
    reader = PdfReader(io.BytesIO(stored))
    assert reader.is_encrypted, "the stored object was replaced with decrypted content"

    with pytest.raises(Exception):  # noqa: B017 - unreadable without the password is the point
        _ = reader.pages[0].extract_text()


# ------------------------------------------------------------------ downloads


def test_download_requires_a_token(client: TestClient, app_id: str, corpus_path) -> None:
    document_id = upload(client, app_id, corpus_path("valid_hdfc")).json()["id"]
    assert client.get(f"/documents/{document_id}/download").status_code == 422
    assert client.get(f"/documents/{document_id}/download?token=forged").status_code == 403


def test_download_url_requires_ops_credentials(
    client: TestClient, app_id: str, corpus_path
) -> None:
    document_id = upload(client, app_id, corpus_path("valid_hdfc")).json()["id"]
    assert client.get(f"/documents/{document_id}/download-url").status_code == 401
    assert (
        client.get(f"/documents/{document_id}/download-url", headers={"X-Ops-Secret": "nope"})
    ).status_code == 401


def test_signed_url_downloads_the_document(client: TestClient, app_id: str, corpus_path) -> None:
    document_id = upload(client, app_id, corpus_path("valid_hdfc")).json()["id"]

    minted = client.get(f"/documents/{document_id}/download-url", headers=OPS_HEADERS)
    assert minted.status_code == 200

    downloaded = client.get(minted.json()["url"])
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")
    assert downloaded.headers["cache-control"] == "no-store"


def test_a_token_for_one_document_does_not_open_another(
    client: TestClient, app_id: str, corpus_path
) -> None:
    first = upload(client, app_id, corpus_path("valid_hdfc")).json()["id"]
    second = upload(client, app_id, corpus_path("valid_sbi")).json()["id"]

    token = client.get(f"/documents/{first}/download-url", headers=OPS_HEADERS).json()["url"]
    token = token.split("token=")[1]

    assert client.get(f"/documents/{second}/download?token={token}").status_code == 403


# ------------------------------------------------------------------ audit


def test_ingestion_is_fully_audited(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    document_id = upload(client, app_id, corpus_path("password_protected_icici")).json()["id"]
    drain()

    session.expire_all()
    types = [
        e.event_type
        for e in session.scalars(
            select(DocumentEvent)
            .where(DocumentEvent.document_id == document_id)
            .order_by(DocumentEvent.created_at, DocumentEvent.id)
        ).all()
    ]
    assert EventType.DOCUMENT_RECEIVED in types
    assert EventType.STATUS_CHANGED in types
    assert EventType.PASSWORD_REQUESTED in types


def test_reprocessing_a_decided_document_is_a_no_op(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    """A job can outlive the thing it was queued for; that must not reopen a verdict."""
    document_id = upload(client, app_id, corpus_path("corrupt_truncated_axis")).json()["id"]
    drain()

    from app.providers.queue import DbJobQueue

    DbJobQueue().enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": document_id})
    session.commit()
    drain()

    session.expire_all()
    results = session.scalars(
        select(ValidationResult).where(ValidationResult.document_id == document_id)
    ).all()
    assert len(results) == 1, "a replayed job must not produce a second verdict"
