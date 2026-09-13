"""Demo-only endpoints.

These exist purely so a walkthrough does not require navigating a file picker for every
scenario. They read from a directory, which is the kind of thing that needs a gate and a
test rather than a comment saying it is fine.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.domain.enums import JobType
from app.models.document import Document
from app.services.pipeline import process_document
from app.workers.document_processor import Worker


@pytest.fixture
def app_id(client: TestClient, session: Session) -> str:
    response = client.post(
        "/applications",
        json={
            "external_reference": "FLX-77001",
            "borrower_name": "Rajesh Kumar Sharma",
            "business_name": "Sharma Metal Works Private Limited",
            "phone_number": "+919820009999",
        },
    )
    return response.json()["id"]


def test_samples_describe_what_each_document_proves(client: TestClient) -> None:
    samples = client.get("/demo/samples").json()

    assert len(samples) >= 19
    ids = {s["id"] for s in samples}
    assert {"valid_hdfc", "wrong_period_short_hdfc", "password_protected_icici"} <= ids

    for sample in samples:
        assert sample["expected_outcome"] in {"PASS", "FIX", "REVIEW"}
        assert sample["notes"], "a sample with no explanation is not a demo aid"


def test_submitting_a_sample_runs_the_real_pipeline(
    client: TestClient, session: Session, app_id: str
) -> None:
    """A demo shortcut that bypassed the pipeline would demonstrate the wrong thing."""
    response = client.post(f"/applications/{app_id}/documents/sample", json={"id": "valid_hdfc"})
    assert response.status_code == 202

    document_id = response.json()["id"]
    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: process_document})
    while worker.run_once():
        pass

    status = client.get(f"/documents/{document_id}/status").json()
    assert status["outcome"] == "PASS"

    session.expire_all()
    document = session.get(Document, document_id)
    assert document is not None
    # Same storage path, same checks, same detected type as a browser upload.
    assert document.detected_mime_type == "application/pdf"
    assert document.sha256


def test_a_sample_is_indistinguishable_from_an_upload(
    client: TestClient, session: Session, app_id: str, corpus_path
) -> None:
    sample_id = client.post(
        f"/applications/{app_id}/documents/sample", json={"id": "valid_hdfc"}
    ).json()["id"]

    path = corpus_path("valid_hdfc")
    with path.open("rb") as fh:
        upload_id = client.post(
            f"/applications/{app_id}/documents",
            files={"file": (path.name, fh, "application/pdf")},
        ).json()["id"]

    session.expire_all()
    sample = session.get(Document, sample_id)
    upload = session.get(Document, upload_id)
    assert sample is not None and upload is not None
    assert sample.sha256 == upload.sha256


def test_unknown_sample_is_rejected(client: TestClient, app_id: str) -> None:
    response = client.post(
        f"/applications/{app_id}/documents/sample", json={"id": "../../etc/passwd"}
    )
    assert response.status_code == 404


def test_samples_are_unavailable_when_demo_mode_is_off(client: TestClient) -> None:
    """The endpoint reads from a directory. Outside local development there is no
    reason for it to exist, so it does not."""
    settings = get_settings()
    original = settings.demo_mode
    try:
        settings.demo_mode = False
        assert client.get("/demo/samples").status_code == 404
    finally:
        settings.demo_mode = original


def test_demo_mode_is_reported_in_readiness(client: TestClient) -> None:
    assert "demo_mode" in client.get("/ready").json()["config"]


def test_sample_submission_uses_the_corpus_filename(
    client: TestClient, session: Session, app_id: str
) -> None:
    """The customer-visible filename should read like the document they sent, so the
    thread makes sense when someone reads it back."""
    document_id = client.post(
        f"/applications/{app_id}/documents/sample", json={"id": "wrong_period_short_hdfc"}
    ).json()["id"]

    session.expire_all()
    document = session.scalars(select(Document).where(Document.id == document_id)).one()
    assert document.original_filename == "wrong_period_short_hdfc.pdf"
