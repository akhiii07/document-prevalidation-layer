"""State machine invariants.

These assert against the transition table itself rather than trusting the comments
around it, so the guarantees survive future edits to the table.
"""

from __future__ import annotations

import pytest

from app.domain.enums import (
    TERMINAL_DOCUMENT_STATUSES,
    DocumentStatus,
    Outcome,
    RequirementStatus,
)
from app.domain.state_machine import (
    DOCUMENT_TRANSITIONS,
    IllegalTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
    reachable_from,
    requirement_status_for,
    status_for_outcome,
)

S = DocumentStatus


# ------------------------------------------------------------------ the core invariant


def test_passed_is_only_reachable_from_a_decision() -> None:
    """The product's central promise: uncertainty never resolves into PASS.

    PASSED may be entered only from VALIDATING (rules reached a verdict) or IN_REVIEW
    (a human decided). Any other edge into PASSED would mean a document was accepted
    without either of those happening.
    """
    sources = {src for src, targets in DOCUMENT_TRANSITIONS.items() if S.PASSED in targets}
    assert sources == {S.VALIDATING, S.IN_REVIEW}


def test_no_shortcut_from_intake_to_passed() -> None:
    for status in (S.RECEIVED, S.INGESTING, S.AWAITING_PASSWORD, S.EXTRACTING, S.EXTRACTED):
        assert not can_transition(status, S.PASSED), f"{status} must not reach PASSED directly"


def test_every_non_terminal_state_can_reach_a_human() -> None:
    """Processing can fail at any stage. A document nobody is waiting on is the one
    outcome the product must never produce."""
    for status, targets in DOCUMENT_TRANSITIONS.items():
        if is_terminal(status) or status is S.IN_REVIEW:
            continue
        assert S.IN_REVIEW in targets, f"{status} cannot escalate to a human"


def test_every_state_is_reachable_from_received() -> None:
    """An unreachable state is dead code pretending to be a design."""
    reachable = reachable_from(S.RECEIVED) | {S.RECEIVED}
    assert reachable == set(DocumentStatus)


def test_every_state_reaches_a_terminal_state() -> None:
    """No cycles that a document could get stuck in."""
    for status in DocumentStatus:
        if is_terminal(status):
            continue
        assert reachable_from(status) & TERMINAL_DOCUMENT_STATUSES, f"{status} never terminates"


# ------------------------------------------------------------------ mechanics


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for status in TERMINAL_DOCUMENT_STATUSES:
        assert DOCUMENT_TRANSITIONS[status] == frozenset()


def test_transition_table_covers_every_status() -> None:
    assert set(DOCUMENT_TRANSITIONS) == set(DocumentStatus)


def test_illegal_transition_raises_with_a_useful_message() -> None:
    with pytest.raises(IllegalTransitionError) as exc:
        assert_transition(S.RECEIVED, S.PASSED)

    message = str(exc.value)
    assert "RECEIVED -> PASSED" in message
    assert "INGESTING" in message, "the error should say what *is* allowed"


def test_terminal_transition_error_says_terminal() -> None:
    with pytest.raises(IllegalTransitionError, match="terminal"):
        assert_transition(S.PASSED, S.VALIDATING)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(Outcome.PASS, S.PASSED), (Outcome.FIX, S.NEEDS_FIX), (Outcome.REVIEW, S.IN_REVIEW)],
)
def test_outcome_maps_to_status(outcome: Outcome, expected: DocumentStatus) -> None:
    assert status_for_outcome(outcome) is expected


def test_outcome_mapping_is_total() -> None:
    """Adding a fourth outcome must break loudly, not silently."""
    assert {status_for_outcome(o) for o in Outcome} == {S.PASSED, S.NEEDS_FIX, S.IN_REVIEW}


# ------------------------------------------------------------------ requirement view


def test_needs_fix_leaves_the_requirement_waiting_on_the_customer() -> None:
    """The submission is finished; the obligation is not. This distinction is what
    makes the correction loop and the pendency metric work (ADR-006)."""
    assert requirement_status_for(S.NEEDS_FIX) is RequirementStatus.AWAITING_CUSTOMER_ACTION


def test_requirement_projection_covers_every_document_status() -> None:
    for status in DocumentStatus:
        assert isinstance(requirement_status_for(status), RequirementStatus)


def test_only_passed_clears_a_requirement() -> None:
    cleared = [s for s in DocumentStatus if requirement_status_for(s) is RequirementStatus.CLEARED]
    assert cleared == [S.PASSED]
