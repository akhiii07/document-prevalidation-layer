"""FastAPI application factory.

Modular monolith (ADR-004): one process, internally partitioned into
api / services / validators / providers / domain / models / workers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import applications, demo, documents, health, reviews
from app.config.rules import get_rules
from app.config.settings import get_settings
from app.domain.enums import JobType
from app.services.pipeline import process_document
from app.workers.document_processor import Worker

logger = logging.getLogger("docverify")


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    _configure_logging(settings.debug)

    # Fail fast: a process that cannot parse its validation rules must not start.
    # Starting anyway would mean issuing verdicts from implicit defaults.
    rules = get_rules()

    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    logger.info("starting %s", settings.app_name)
    logger.info("config: %s", settings.safe_summary())
    logger.info("validation rules: v%s for %s", rules.version, rules.document_type)

    # The worker runs in-process: one deployable unit, and jobs commit in the same
    # database as the state transitions they cause (ADR-003).
    worker: Worker | None = None
    if settings.run_worker:
        worker = Worker(handlers={JobType.PROCESS_DOCUMENT: process_document})
        worker.start()
        app.state.worker = worker

    yield

    if worker is not None:
        worker.stop()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Real-time document collection and pre-validation for MSME lending. "
            "Returns PASS / FIX / REVIEW with a specific reason and a specific next action."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(applications.router)
    app.include_router(documents.router)
    app.include_router(reviews.router)
    app.include_router(demo.router)
    return app


app = create_app()
