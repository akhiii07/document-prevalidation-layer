from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Set before any application import so the cached Settings pick these up.
_TMP = Path(tempfile.mkdtemp(prefix="docverify-test-"))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("STORAGE_DIR", str(_TMP / "documents"))
# Tests must pass with no LLM credentials present (ADR-010).
os.environ.pop("ANTHROPIC_API_KEY", None)
# Tests drive the worker one job at a time rather than racing a background thread.
os.environ.setdefault("RUN_WORKER", "false")

from corpus.generator.build import DEFAULT_OUT, build  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: F401,E402  - registers models on Base.metadata
from app.db.base import Base  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.application import Application, DocumentRequirement  # noqa: E402
from app.services.intake import create_application, request_document  # noqa: E402

CORPUS_DIR = DEFAULT_OUT
CORPUS_MANIFEST = CORPUS_DIR / "manifest.json"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create the schema once for the whole test session.

    `create_all` rather than running migrations: the migration itself is verified
    separately in `test_migrations.py`, and coupling every unit test to Alembic would
    make failures harder to read.
    """
    Base.metadata.create_all(get_engine())
    yield
    Base.metadata.drop_all(get_engine())


@pytest.fixture
def session() -> Iterator[Session]:
    """A session whose writes are discarded after each test."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.rollback()
        # Tables are shared across tests, so clear rows rather than the schema.
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
        db.close()


@pytest.fixture
def application(session: Session) -> Application:
    return create_application(
        session,
        external_reference="FLX-10231",
        borrower_name="Rajesh Kumar Sharma",
        business_name="Sharma Metal Works Private Limited",
        phone_number="+919820000000",
    )


@pytest.fixture
def requirement(session: Session, application: Application) -> DocumentRequirement:
    return request_document(session, application)


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="session")
def manifest() -> dict:
    """The synthetic corpus manifest, built on demand.

    Building here rather than requiring a manual step means a clean clone can run the
    whole suite with `pytest` alone.
    """
    import json
    from datetime import UTC, datetime

    if not CORPUS_MANIFEST.exists():
        build(datetime.now(UTC).date(), 20260912, CORPUS_DIR)
    return json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def by_id(manifest: dict) -> dict[str, dict]:
    return {d["id"]: d for d in manifest["documents"]}


@pytest.fixture(scope="session")
def corpus_path(by_id: dict[str, dict]):
    """Resolve a corpus document id to its file."""

    def _resolve(doc_id: str) -> Path:
        return CORPUS_DIR / by_id[doc_id]["file"]

    return _resolve
