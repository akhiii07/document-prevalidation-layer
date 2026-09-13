"""Job queue and worker behaviour.

The properties worth guaranteeing: work is not lost when a process dies, work is not
done twice, and a document whose processing fails permanently ends up in front of a
human rather than silently stopping.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.domain.enums import DocumentStatus, EventType, JobStatus, JobType, ReviewStatus
from app.models.application import DocumentRequirement
from app.models.document import Document
from app.models.event import DocumentEvent
from app.models.job import Job
from app.models.review import Review
from app.providers.queue import DbJobQueue
from app.services.document_state import transition
from app.services.intake import create_submission
from app.workers.document_processor import Worker


@pytest.fixture
def queue() -> DbJobQueue:
    return DbJobQueue()


def _submit(session: Session, requirement: DocumentRequirement) -> Document:
    return create_submission(
        session,
        requirement,
        storage_key=f"docs/key-{requirement.submission_count + 1}",
        original_filename="statement.pdf",
        file_size=2048,
    )


# ------------------------------------------------------------------ enqueue / claim


def test_enqueued_job_can_be_claimed(session: Session, queue: DbJobQueue) -> None:
    job = queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"})
    assert job.status == JobStatus.QUEUED

    claimed = queue.claim(session, "worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.locked_by == "worker-1"
    assert claimed.attempts == 1


def test_a_claimed_job_is_not_claimed_again(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"})
    assert queue.claim(session, "worker-1") is not None
    assert queue.claim(session, "worker-2") is None


def test_dedupe_key_prevents_duplicate_work(session: Session, queue: DbJobQueue) -> None:
    """A customer double-tapping upload must not cause two processing runs."""
    first = queue.enqueue(
        session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"}, dedupe_key="doc:abc"
    )
    second = queue.enqueue(
        session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"}, dedupe_key="doc:abc"
    )
    assert first.id == second.id
    assert session.scalar(select(Job).where(Job.dedupe_key == "doc:abc").limit(1)) is not None
    assert len(session.scalars(select(Job)).all()) == 1


def test_dedupe_allows_a_new_job_once_the_previous_one_finished(
    session: Session, queue: DbJobQueue
) -> None:
    """A re-upload of the same document must be processable again."""
    first = queue.enqueue(
        session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"}, dedupe_key="doc:abc"
    )
    queue.complete(session, first)

    second = queue.enqueue(
        session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"}, dedupe_key="doc:abc"
    )
    assert second.id != first.id


def test_delayed_jobs_are_invisible_until_due(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {}, delay_seconds=3600)
    assert queue.claim(session, "worker-1") is None


def test_claim_respects_job_type_filter(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {})
    assert queue.claim(session, "worker-1", job_types=["SOMETHING_ELSE"]) is None


# ------------------------------------------------------------------ failure handling


def test_failure_schedules_a_retry_with_backoff(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {})
    job = queue.claim(session, "worker-1")
    assert job is not None

    queue.fail(session, job, "transient extraction error")

    assert job.status == JobStatus.FAILED
    assert job.available_at > utcnow(), "a retry must be delayed, not immediate"
    assert job.locked_by is None
    assert "transient" in (job.last_error or "")


def test_retries_are_exhausted_into_dead(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {}, max_attempts=2)

    for attempt in range(1, 3):
        job = queue.claim(session, "worker-1")
        assert job is not None, f"attempt {attempt} could not claim the job"
        queue.fail(session, job, "boom")
        # fail() schedules a backoff; rewind past it so the test does not sleep.
        job.available_at = utcnow() - timedelta(seconds=1)
        session.flush()

    assert job.status == JobStatus.DEAD
    assert queue.claim(session, "worker-1") is None, "a dead job must not be retried"


def test_stale_lease_is_reclaimed(session: Session, queue: DbJobQueue) -> None:
    """A worker killed mid-job must not strand its document forever."""
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {})
    job = queue.claim(session, "worker-that-dies")
    assert job is not None

    job.locked_at = utcnow() - timedelta(hours=1)
    session.flush()

    assert queue.reclaim_stale(session, lease_seconds=60) == 1
    recovered = queue.claim(session, "worker-2")
    assert recovered is not None and recovered.id == job.id


def test_running_jobs_within_their_lease_are_left_alone(
    session: Session, queue: DbJobQueue
) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {})
    queue.claim(session, "worker-1")
    assert queue.reclaim_stale(session, lease_seconds=3600) == 0


# ------------------------------------------------------------------ worker


def test_worker_processes_a_job(session: Session, queue: DbJobQueue) -> None:
    handled: list[str] = []
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"})
    session.commit()

    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: lambda s, j: handled.append(j.id)})
    assert worker.run_once() is True
    assert len(handled) == 1

    session.expire_all()
    job = session.scalars(select(Job)).one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.completed_at is not None


def test_worker_reports_no_work_when_queue_is_empty() -> None:
    assert Worker().run_once() is False


def test_worker_records_a_failure_and_retries(session: Session, queue: DbJobQueue) -> None:
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"})
    session.commit()

    def explode(s: Session, j: Job) -> None:
        raise ValueError("extraction exploded")

    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: explode})
    assert worker.run_once() is True

    session.expire_all()
    job = session.scalars(select(Job)).one()
    assert job.status == JobStatus.FAILED
    assert "extraction exploded" in (job.last_error or "")


def test_permanently_failed_document_goes_to_a_human(
    session: Session, requirement: DocumentRequirement, queue: DbJobQueue
) -> None:
    """The failure the product exists to prevent is a customer hearing nothing.

    When retries are exhausted nothing further happens on its own, so the document must
    land in the Operations queue rather than sitting in a working state forever.
    """
    document = _submit(session, requirement)
    transition(session, document, DocumentStatus.INGESTING)
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": document.id}, max_attempts=1)
    session.commit()

    def explode(s: Session, j: Job) -> None:
        raise RuntimeError("extractor unavailable")

    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: explode})
    worker.run_once()

    session.expire_all()
    document = session.get(Document, document.id)
    assert document is not None
    assert document.status == DocumentStatus.IN_REVIEW

    review = session.scalars(select(Review)).one()
    assert review.reason_code == "PROCESSING_ERROR"
    assert review.status == ReviewStatus.OPEN

    event_types = {
        e.event_type
        for e in session.scalars(
            select(DocumentEvent).where(DocumentEvent.document_id == document.id)
        ).all()
    }
    assert EventType.PROCESSING_FAILED in event_types
    assert EventType.REVIEW_OPENED in event_types


def test_dead_job_does_not_disturb_an_already_decided_document(
    session: Session, requirement: DocumentRequirement, queue: DbJobQueue
) -> None:
    """A late-arriving failure must not reopen a document a human already settled."""
    document = _submit(session, requirement)
    for target in (
        DocumentStatus.INGESTING,
        DocumentStatus.EXTRACTING,
        DocumentStatus.EXTRACTED,
        DocumentStatus.VALIDATING,
        DocumentStatus.PASSED,
    ):
        transition(session, document, target)
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": document.id}, max_attempts=1)
    session.commit()

    def explode(s: Session, j: Job) -> None:
        raise RuntimeError("late")

    worker = Worker(handlers={JobType.PROCESS_DOCUMENT: explode})
    worker.run_once()

    session.expire_all()
    document = session.get(Document, document.id)
    assert document is not None
    assert document.status == DocumentStatus.PASSED
    assert session.scalars(select(Review)).all() == []


def test_job_survives_a_worker_restart(session: Session, queue: DbJobQueue) -> None:
    """The reason the queue lives in the database rather than in memory (ADR-003).

    A worker claims a job and disappears. A new worker starts, reclaims the stale lease,
    and the work still gets done.
    """
    queue.enqueue(session, JobType.PROCESS_DOCUMENT, {"document_id": "abc"})
    session.commit()

    dying = Worker(handlers={JobType.PROCESS_DOCUMENT: lambda s, j: None}, worker_id="worker-1")
    claimed = dying.queue.claim(session, dying.worker_id)
    assert claimed is not None
    claimed.locked_at = utcnow() - timedelta(hours=1)
    session.commit()  # the process dies here, mid-job

    handled: list[str] = []
    restarted = Worker(
        handlers={JobType.PROCESS_DOCUMENT: lambda s, j: handled.append(j.id)},
        worker_id="worker-2",
    )
    assert restarted.sweep_stale() == 1
    assert restarted.run_once() is True
    assert handled == [claimed.id]

    session.expire_all()
    job = session.scalars(select(Job)).one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 2, "the redelivery counts as a second attempt"
