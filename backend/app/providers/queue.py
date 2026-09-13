"""Job queue.

`JobQueue` is the seam. `DbJobQueue` is the MVP implementation; a Pub/Sub implementation
would satisfy the same protocol without callers changing (ADR-003).

Claiming is done with a conditional UPDATE rather than `SELECT ... FOR UPDATE SKIP
LOCKED`. SKIP LOCKED is the better tool on PostgreSQL but does not exist on SQLite, and
a queue that behaves differently on the development database than in deployment is a
source of bugs that only appear in production. The conditional update is portable and
still race-free: two workers may both select the same candidate, but only one `UPDATE
... WHERE status = 'QUEUED'` can affect a row.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.domain.enums import JobStatus
from app.models.job import Job

DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 5


class JobQueue(Protocol):
    def enqueue(
        self,
        session: Session,
        job_type: str,
        payload: dict,
        *,
        dedupe_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        delay_seconds: int = 0,
    ) -> Job: ...

    def claim(
        self,
        session: Session,
        worker_id: str,
        *,
        job_types: list[str] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Job | None: ...

    def complete(self, session: Session, job: Job) -> None: ...

    def fail(self, session: Session, job: Job, error: str) -> None: ...

    def reclaim_stale(
        self, session: Session, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> int: ...


class DbJobQueue:
    """Queue backed by the `jobs` table.

    The reason this lives in the application database rather than a broker: a job and
    the document state transition it causes commit in the same transaction, so the queue
    can never claim work for a document whose state says otherwise.
    """

    def enqueue(
        self,
        session: Session,
        job_type: str,
        payload: dict,
        *,
        dedupe_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        delay_seconds: int = 0,
    ) -> Job:
        if dedupe_key is not None:
            existing = session.scalars(
                select(Job).where(
                    Job.job_type == job_type,
                    Job.dedupe_key == dedupe_key,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
                )
            ).first()
            if existing is not None:
                # Re-enqueueing the same work is a no-op, not a second job. A customer
                # double-tapping upload must not cause the document to be processed twice.
                return existing

        job = Job(
            job_type=job_type,
            payload=payload,
            dedupe_key=dedupe_key,
            max_attempts=max_attempts,
            available_at=utcnow() + timedelta(seconds=delay_seconds),
        )
        session.add(job)
        session.flush()
        return job

    def claim(
        self,
        session: Session,
        worker_id: str,
        *,
        job_types: list[str] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Job | None:
        now = utcnow()
        query = select(Job).where(
            Job.status.in_([JobStatus.QUEUED, JobStatus.FAILED]),
            Job.available_at <= now,
        )
        if job_types:
            query = query.where(Job.job_type.in_(job_types))
        query = query.order_by(Job.available_at).limit(10)

        for candidate in session.scalars(query).all():
            won = session.execute(
                update(Job)
                .where(
                    Job.id == candidate.id,
                    # The guard that makes this race-free: only one UPDATE can match.
                    Job.status.in_([JobStatus.QUEUED, JobStatus.FAILED]),
                )
                .values(
                    status=JobStatus.RUNNING,
                    locked_at=now,
                    locked_by=worker_id,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            ).rowcount
            if won == 1:
                session.flush()
                session.refresh(candidate)
                return candidate
        return None

    def complete(self, session: Session, job: Job) -> None:
        job.status = JobStatus.SUCCEEDED
        job.completed_at = utcnow()
        job.locked_at = None
        job.locked_by = None
        job.last_error = None
        session.flush()

    def fail(self, session: Session, job: Job, error: str) -> None:
        """Record a failure, scheduling a retry or dead-lettering.

        A dead job is never silently dropped: the caller is responsible for routing the
        affected document to REVIEW, because a document the system failed to process is
        still a document a customer is waiting on.
        """
        job.last_error = error[:1000]
        job.locked_at = None
        job.locked_by = None

        if job.attempts >= job.max_attempts:
            job.status = JobStatus.DEAD
            job.completed_at = utcnow()
        else:
            job.status = JobStatus.FAILED
            # Exponential backoff: 5s, 10s, 20s, ...
            job.available_at = utcnow() + timedelta(
                seconds=BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1))
            )
        session.flush()

    def reclaim_stale(self, session: Session, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> int:
        """Return jobs whose worker died mid-flight to the queue.

        Without this a process killed while holding a lease would strand its document in
        a non-terminal state forever, with no error and nobody waiting on it.
        """
        cutoff = utcnow() - timedelta(seconds=lease_seconds)
        result = session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
            .values(
                status=JobStatus.FAILED,
                locked_at=None,
                locked_by=None,
                last_error="lease expired; worker presumed dead",
                available_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.flush()
        return int(result.rowcount or 0)


def get_queue() -> JobQueue:
    return DbJobQueue()
