"""Validation rule types.

A rule returns a `RuleResult` carrying not just a verdict but the **evidence** it was
built from -- the actual values compared. Both the customer message and the Operations
card are rendered from that evidence rather than re-deriving anything, so what the
customer is told and what a reviewer sees are guaranteed to describe the same finding.

`NOT_EVALUATED` is a first-class state, distinct from `PASS`. A rule that could not run
has not been satisfied; treating the two as the same is how a document with half its
fields unreadable ends up looking fully checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.domain.extraction import ExtractedStatement


class RuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    #: The data the rule needed was not extractable. Not a failure -- it lowers rule
    #: coverage, which lowers composite confidence, which pushes toward REVIEW.
    NOT_EVALUATED = "NOT_EVALUATED"
    #: A non-blocking observation. Can contribute to REVIEW; can never cause a failure.
    SIGNAL = "SIGNAL"


@dataclass
class RuleResult:
    rule_id: str
    status: RuleStatus
    reason_code: str | None = None
    #: Only blocking rules can change an outcome. Signals only inform.
    blocking: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)
    #: How sure the rule is about *its own* finding, independent of how well the rest of
    #: the document was read. Gates whether a FIX may be issued on this rule alone.
    evidence_confidence: float = 1.0

    @property
    def failed(self) -> bool:
        return self.status is RuleStatus.FAIL

    def to_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "blocking": self.blocking,
            "evidence": self.evidence,
            "evidence_confidence": round(self.evidence_confidence, 4),
        }


@dataclass
class ValidationContext:
    """Everything the rules are allowed to see."""

    statement: ExtractedStatement
    #: The legal/registered business name on the application, for the identity check.
    business_name: str
    #: Recency is measured against the application, not the wall clock: anchoring to
    #: "today" would let a document that validated correctly on Monday fail on
    #: re-validation weeks later, through no fault of the customer.
    reference_date: date
    #: PDF-level observations collected before extraction.
    integrity_signals: list[RuleResult] = field(default_factory=list)


@dataclass
class Verdict:
    outcome: str  # PASS | FIX | REVIEW
    primary_reason_code: str | None
    secondary_reason_codes: list[str]
    results: list[RuleResult]
    composite_confidence: float
    field_score: float
    rule_coverage: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def primary_evidence(self) -> dict[str, Any]:
        """Evidence behind the primary reason, merged with the verdict's own.

        The customer message, the Operations card and the audit event are all rendered
        from this one dict, so they cannot describe different findings.
        """
        for result in self.results:
            if result.reason_code and result.reason_code == self.primary_reason_code:
                return {**result.evidence, **self.evidence}
        return dict(self.evidence)
