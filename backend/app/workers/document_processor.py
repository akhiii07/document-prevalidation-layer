"""Background worker.

Runs in-process alongside the API (ADR-003/ADR-004). One job is claimed, handled, and
committed at a time.

The part that matters most is `_dead_letter`. A document whose processing failed
permanently must not simply stop: from the customer's point of view they sent a document
and nothing happened, which is the exact failure this product exists to remove. So an
exhausted job routes its document to REVIEW, where a human sees it.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_sessionmaker
from app.domain.enums import DocumentStatus, EventType, JobStatus, JobType, ReviewStatus
from app.models.document import Document
from app.models.job import Job
from app.models.review import Review
from app.providers.queue import DEFAULT_LEASE_SECONDS, DbJobQueue, JobQueue
from app.services.audit import record_event
from app.services.document_state import transition

logger = logging.getLogger("docverify.worker")

JobHandler = Callable[[Session, Job], None]

POLL_INTERVAL_SECONDS = 1.0
STALE_SWEEP_INTERVAL_SECONDS = 60.0


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _default_handlers() -> dict[str, JobHandler]:
    # Imported lazily: the pipeline imports services that import models, and a top-level
    # import here would make the worker module a hub in that cycle.
    from app.services.pipeline import process_document

    return {JobType.PROCESS_DOCUMENT: process_document}


class Worker:
    def __init__(
        self,
        *,
        handlers: dict[str, JobHandler] | None = None,
        queue: JobQueue | None = None,
        session_factory: sessionmaker[Session] | None = None,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.handlers = handlers if handlers is not None else _default_handlers()
        self.queue = queue or DbJobQueue()
        self.session_factory = session_factory or get_sessionmaker()
        self.worker_id = worker_id or _worker_id()
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ one job

    def run_once(self) -> bool:
        """Claim and process at most one job. Returns True if a job was handled."""
        session = self.session_factory()
        try:
            job = self.queue.claim(
                session,
                self.worker_id,
                job_types=list(self.handlers),
                lease_seconds=self.lease_seconds,
            )
            if job is None:
                session.commit()
                return False

            # The claim is committed before the handler runs. If the process dies
            # mid-handler the attempt is not lost, and `reclaim_stale` can return the
            # job to the queue rather than leaving it RUNNING forever.
            session.commit()

            handler = self.handlers.get(job.job_type)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler registered for job type {job.job_type}")
                handler(session, job)
                self.queue.complete(session, job)
                session.commit()
            except Exception as exc:  # noqa: BLE001 - the queue is the error boundary
                session.rollback()
                logger.exception("job %s failed", job.id)
                self._record_failure(job.id, exc)
            return True
        finally:
            session.close()

    def _record_failure(self, job_id: str, exc: Exception) -> None:
        """Record the failure in a fresh session.

        The handler's session was rolled back and may hold partially-applied state, so
        reusing it to record the failure could roll the failure record back too.
        """
        session = self.session_factory()
        try:
            job = session.get(Job, job_id)
            if job is None:
                return
            self.queue.fail(session, job, f"{type(exc).__name__}: {exc}")
            if job.status == JobStatus.DEAD:
                self._dead_letter(session, job)
            session.commit()
        finally:
            session.close()

    def _dead_letter(self, session: Session, job: Job) -> None:
        """Route a permanently failed document to a human.

        Retries are exhausted, so nothing further will happen on its own. Leaving the
        document in a working state would mean a customer waiting indefinitely on a
        submission nobody is looking at.
        """
        document_id = (job.payload or {}).get("document_id")
        if not document_id:
            return

        document = session.get(Document, document_id)
        if document is None or DocumentStatus(document.status) in {
            DocumentStatus.PASSED,
            DocumentStatus.NEEDS_FIX,
            DocumentStatus.SUPERSEDED,
            DocumentStatus.IN_REVIEW,
        }:
            return

        transition(
            session,
            document,
            DocumentStatus.IN_REVIEW,
            event_type=EventType.PROCESSING_FAILED,
            payload={"job_id": job.id, "attempts": job.attempts, "error": job.last_error},
        )
        session.add(
            Review(
                document_id=document.id,
                reason_code="PROCESSING_ERROR",
                status=ReviewStatus.OPEN,
            )
        )
        record_event(
            session,
            event_type=EventType.REVIEW_OPENED,
            document_id=document.id,
            requirement_id=document.requirement_id,
            payload={"reason_code": "PROCESSING_ERROR", "job_id": job.id},
        )
        session.flush()

    # ------------------------------------------------------------------ loop

    def sweep_stale(self) -> int:
        session = self.session_factory()
        try:
            count = self.queue.reclaim_stale(session, lease_seconds=self.lease_seconds)
            session.commit()
            if count:
                logger.warning("reclaimed %d stale job(s)", count)
            return count
        finally:
            session.close()

    def run_forever(self, poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
        logger.info("worker %s started", self.worker_id)
        last_sweep = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_sweep > STALE_SWEEP_INTERVAL_SECONDS:
                self.sweep_stale()
                last_sweep = now

            try:
                did_work = self.run_once()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("worker loop error")
                did_work = False

            if not did_work:
                self._stop.wait(poll_interval)
        logger.info("worker %s stopped", self.worker_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever, name="docverify-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
