"""Demo-only endpoints.

These exist so a walkthrough does not require navigating a file picker to
`corpus/generated` for every scenario. They are gated on `demo_mode`, which is off
outside local development: an endpoint that reads files from a directory does not belong
in a deployed service without a reason, and here there is none beyond convenience.

The corpus is synthetic by construction, so nothing sensitive is exposed -- but the path
is still resolved and checked against the corpus root, because "the directory only
contains safe files" is an assumption that ages badly.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import DocumentRead
from app.config.settings import Settings, get_settings
from app.db.session import get_db
from app.models.application import Application
from app.services.ingestion import ingest

router = APIRouter(tags=["demo"])


class SampleDocument(BaseModel):
    id: str
    file: str
    scenario: str
    expected_outcome: str
    expected_reason_code: str | None
    password: str | None
    notes: str


class SampleRequest(BaseModel):
    id: str


def require_demo_mode(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.demo_mode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return settings


def _manifest(settings: Settings) -> dict:
    path = settings.corpus_dir / "manifest.json"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="the synthetic corpus has not been generated; "
            "run `python -m corpus.generator.build`",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(settings: Settings, filename: str) -> Path:
    root = settings.corpus_dir.resolve()
    candidate = (root / filename).resolve()
    # The filename comes from the manifest, not the caller -- but checking costs nothing
    # and holds if that ever stops being true.
    if not candidate.is_relative_to(root) or not candidate.exists():
        raise HTTPException(status_code=404, detail="sample not found")
    return candidate


@router.get("/demo/samples", response_model=list[SampleDocument])
def list_samples(settings: Settings = Depends(require_demo_mode)) -> list[SampleDocument]:
    """The corpus, with what each document is expected to demonstrate."""
    return [
        SampleDocument(
            id=doc["id"],
            file=doc["file"],
            scenario=doc["scenario"],
            expected_outcome=doc["expected"]["outcome"],
            expected_reason_code=doc["expected"]["primary_reason_code"],
            password=doc.get("password"),
            notes=doc["notes"],
        )
        for doc in _manifest(settings)["documents"]
    ]


@router.post(
    "/applications/{application_id}/documents/sample",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_sample(
    application_id: str,
    payload: SampleRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(require_demo_mode),
) -> DocumentRead:
    """Submit a corpus document as though the customer had sent it.

    Deliberately goes through the same `ingest()` path as a real upload -- same file
    checks, same queue, same worker. A demo shortcut that bypassed the pipeline would
    demonstrate the wrong thing.
    """
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    entry = next(
        (d for d in _manifest(settings)["documents"] if d["id"] == payload.id),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="sample not found")

    path = _resolve(settings, entry["file"])
    with path.open("rb") as handle:
        document, _ = ingest(
            session,
            application,
            source=handle,
            original_filename=entry["file"],
            declared_mime_type="application/pdf",
        )
    return DocumentRead.model_validate(document)
