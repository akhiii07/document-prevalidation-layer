"""Domain vocabulary.

Two deliberate separations here:

1. **Document status vs. requirement status.** A `document` is one *submission*; a
   `document_requirement` is the obligation ("this application owes a bank statement").
   A submission reaching NEEDS_FIX is finished; the requirement is not (ADR-006).

2. **Status vs. outcome.** PASS / FIX / REVIEW is the customer-facing contract
   (`PRODUCT_SPEC.md` section 7). Statuses are internal machinery. Keeping them apart
   means the internal model can gain states without changing the product contract.

Reason codes are deliberately *not* an enum: `validation_rules.yaml` is their single
source of truth, and duplicating the list here would let the two drift.
"""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    """The customer-facing contract. Exactly three values, forever."""

    PASS = "PASS"
    FIX = "FIX"
    REVIEW = "REVIEW"


class DocumentStatus(StrEnum):
    """Lifecycle of a single submission."""

    RECEIVED = "RECEIVED"
    INGESTING = "INGESTING"
    AWAITING_PASSWORD = "AWAITING_PASSWORD"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    VALIDATING = "VALIDATING"

    # Terminal for this submission.
    PASSED = "PASSED"
    NEEDS_FIX = "NEEDS_FIX"
    SUPERSEDED = "SUPERSEDED"

    # Not terminal: a human decision moves it to PASSED or NEEDS_FIX.
    IN_REVIEW = "IN_REVIEW"


TERMINAL_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.PASSED, DocumentStatus.NEEDS_FIX, DocumentStatus.SUPERSEDED}
)

#: Statuses where the system is waiting on someone rather than working.
WAITING_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.AWAITING_PASSWORD, DocumentStatus.IN_REVIEW}
)


class RequirementStatus(StrEnum):
    """Lifecycle of the obligation, across however many submissions it takes."""

    PENDING = "PENDING"  # requested, nothing submitted yet
    IN_PROGRESS = "IN_PROGRESS"  # a submission is being processed
    AWAITING_CUSTOMER_ACTION = "AWAITING_CUSTOMER_ACTION"  # a FIX was issued
    AWAITING_REVIEW = "AWAITING_REVIEW"  # with Operations
    CLEARED = "CLEARED"  # satisfied
    CANCELLED = "CANCELLED"  # no longer required


class ApplicationStatus(StrEnum):
    OPEN = "OPEN"
    DOCUMENTS_COMPLETE = "DOCUMENTS_COMPLETE"
    CANCELLED = "CANCELLED"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    #: The document was superseded by a newer submission before a human got to it.
    #: Distinct from RESOLVED because nobody decided anything -- the question stopped
    #: being worth answering.
    WITHDRAWN = "WITHDRAWN"


class ReviewerAction(StrEnum):
    """What a human decided. Mirrors the two buttons in `PRODUCT_SPEC.md` section 20."""

    ACCEPT = "ACCEPT"
    REQUEST_NEW_DOCUMENT = "REQUEST_NEW_DOCUMENT"


class TextLayer(StrEnum):
    """How the content was obtained. Drives which extractor ran and the confidence floor."""

    NATIVE = "NATIVE"
    OCR = "OCR"
    NONE = "NONE"


class EventType(StrEnum):
    """Append-only audit vocabulary.

    Every metric in `PRODUCT_SPEC.md` section 24 is computed from these, and events
    outlive the documents they describe so that content can be purged on a retention
    schedule without destroying the audit trail (`RESEARCH_REGULATORY.md` section 2).
    """

    REQUIREMENT_CREATED = "REQUIREMENT_CREATED"
    DOCUMENT_RECEIVED = "DOCUMENT_RECEIVED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PASSWORD_REQUESTED = "PASSWORD_REQUESTED"
    PASSWORD_ACCEPTED = "PASSWORD_ACCEPTED"
    PASSWORD_REJECTED = "PASSWORD_REJECTED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    FIX_REQUESTED = "FIX_REQUESTED"
    REVIEW_OPENED = "REVIEW_OPENED"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    REVIEW_WITHDRAWN = "REVIEW_WITHDRAWN"
    REQUIREMENT_CLEARED = "REQUIREMENT_CLEARED"
    DOCUMENT_SUPERSEDED = "DOCUMENT_SUPERSEDED"
    #: Content removed under the retention policy. The event survives the content it
    #: describes -- that asymmetry is the reason this table exists separately.
    DOCUMENT_PURGED = "DOCUMENT_PURGED"
    SALES_NOTIFIED = "SALES_NOTIFIED"
    MESSAGE_SENT = "MESSAGE_SENT"
    PROCESSING_FAILED = "PROCESSING_FAILED"


class MessageChannel(StrEnum):
    """Who a message is for.

    The customer thread is the simulated WhatsApp conversation; the sales thread is the
    mock downstream notification. Modelling both as messages keeps one auditable record
    of everything the system said to anyone.
    """

    CUSTOMER = "CUSTOMER"
    SALES = "SALES"


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageKind(StrEnum):
    INSTRUCTION = "INSTRUCTION"
    DOCUMENT_RECEIVED = "DOCUMENT_RECEIVED"
    PASSWORD_REQUEST = "PASSWORD_REQUEST"
    PASSWORD_SUBMITTED = "PASSWORD_SUBMITTED"
    VALIDATION_RESULT = "VALIDATION_RESULT"
    SUBMISSION_SUPERSEDED = "SUBMISSION_SUPERSEDED"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    SALES_NOTIFICATION = "SALES_NOTIFICATION"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # will be retried
    DEAD = "DEAD"  # retries exhausted; needs human attention


class JobType(StrEnum):
    PROCESS_DOCUMENT = "PROCESS_DOCUMENT"
