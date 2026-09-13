"""Document state machine.

The transition table is explicit and closed. Anything not listed is illegal and raises,
rather than being silently permitted -- an unnoticed illegal transition in a lending
workflow means a document in a state nobody designed for, and an audit trail that no
longer explains how it got there.

The invariant that matters most: PASSED is reachable only from VALIDATING (a verdict
was reached) or IN_REVIEW (a human decided). There is no edge into PASSED from any state
meaning "we could not tell". Uncertainty resolves to REVIEW, always
(`PRODUCT_SPEC.md` section 7). `tests/test_state_machine.py` asserts this against the
table rather than trusting the comment.
"""

from __future__ import annotations

from app.domain.enums import (
    TERMINAL_DOCUMENT_STATUSES,
    DocumentStatus,
    Outcome,
    RequirementStatus,
)


class IllegalTransitionError(RuntimeError):
    """Raised when code attempts a transition the state machine does not define."""

    def __init__(self, current: DocumentStatus, target: DocumentStatus) -> None:
        allowed = sorted(s.value for s in DOCUMENT_TRANSITIONS.get(current, frozenset()))
        super().__init__(
            f"illegal document transition {current.value} -> {target.value}; "
            f"allowed from {current.value}: {allowed or 'none (terminal)'}"
        )
        self.current = current
        self.target = target


S = DocumentStatus

# Note that IN_REVIEW is reachable from every non-terminal state. That is deliberate:
# processing can fail for infrastructure reasons at any stage, and a document stuck in a
# working state with nobody waiting on it is far worse than one extra card in the
# Operations queue. It is also consistent with what REVIEW means -- "the system cannot
# confidently decide" covers "the system could not finish".
DOCUMENT_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    S.RECEIVED: frozenset({S.INGESTING, S.IN_REVIEW, S.SUPERSEDED}),
    # File checks either pass, hit an encrypted file, or fail outright.
    S.INGESTING: frozenset(
        {S.EXTRACTING, S.AWAITING_PASSWORD, S.NEEDS_FIX, S.IN_REVIEW, S.SUPERSEDED}
    ),
    S.AWAITING_PASSWORD: frozenset({S.EXTRACTING, S.NEEDS_FIX, S.IN_REVIEW, S.SUPERSEDED}),
    # An extraction *error* is not a document defect: it goes to a human, not to the
    # customer, because there is no action the customer could usefully take.
    S.EXTRACTING: frozenset({S.EXTRACTED, S.NEEDS_FIX, S.IN_REVIEW, S.SUPERSEDED}),
    S.EXTRACTED: frozenset({S.VALIDATING, S.IN_REVIEW, S.SUPERSEDED}),
    S.VALIDATING: frozenset({S.PASSED, S.NEEDS_FIX, S.IN_REVIEW, S.SUPERSEDED}),
    # Operations decides. Both outcomes are audited.
    S.IN_REVIEW: frozenset({S.PASSED, S.NEEDS_FIX, S.SUPERSEDED}),
    # Terminal.
    S.PASSED: frozenset(),
    S.NEEDS_FIX: frozenset(),
    S.SUPERSEDED: frozenset(),
}

#: States from which a verdict may be issued. Used to keep the outcome mapping honest.
VERDICT_SOURCE_STATUSES: frozenset[DocumentStatus] = frozenset({S.VALIDATING, S.IN_REVIEW})

OUTCOME_TO_STATUS: dict[Outcome, DocumentStatus] = {
    Outcome.PASS: S.PASSED,
    Outcome.FIX: S.NEEDS_FIX,
    Outcome.REVIEW: S.IN_REVIEW,
}


def can_transition(current: DocumentStatus, target: DocumentStatus) -> bool:
    return target in DOCUMENT_TRANSITIONS.get(current, frozenset())


def assert_transition(current: DocumentStatus, target: DocumentStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)


def is_terminal(status: DocumentStatus) -> bool:
    return status in TERMINAL_DOCUMENT_STATUSES


def status_for_outcome(outcome: Outcome) -> DocumentStatus:
    return OUTCOME_TO_STATUS[outcome]


def requirement_status_for(document_status: DocumentStatus) -> RequirementStatus:
    """Project a submission's status onto its requirement.

    The requirement's status is derived rather than independently maintained, so the two
    cannot drift apart. The interesting case is NEEDS_FIX: the *submission* is finished,
    but the *requirement* is now waiting on the customer -- which is exactly the state
    the correction loop and the pendency metric care about.
    """
    match document_status:
        case S.PASSED:
            return RequirementStatus.CLEARED
        case S.NEEDS_FIX:
            return RequirementStatus.AWAITING_CUSTOMER_ACTION
        case S.IN_REVIEW:
            return RequirementStatus.AWAITING_REVIEW
        case S.SUPERSEDED:
            # A superseded submission says nothing about the requirement; the newer
            # submission does. Callers recompute from the active submission.
            return RequirementStatus.IN_PROGRESS
        case _:
            return RequirementStatus.IN_PROGRESS


def reachable_from(status: DocumentStatus) -> set[DocumentStatus]:
    """Every status reachable from `status`. Used by invariant tests."""
    seen: set[DocumentStatus] = set()
    frontier = [status]
    while frontier:
        current = frontier.pop()
        for nxt in DOCUMENT_TRANSITIONS.get(current, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen
