"""R-RED-001 — can the document be read at all?

Runs first, because every other rule's evidence comes from text. A rule reporting "no
period found" on a page nobody could read is reporting our failure as the document's.
"""

from __future__ import annotations

from app.config.rules import ValidationRules
from app.domain.validation import RuleResult, RuleStatus, ValidationContext


def check_readability(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    statement = ctx.statement
    evidence = {
        # Two different facts, kept apart on purpose: how well the pages we read came
        # out, and how much of the document we read at all.
        "readable_page_ratio": round(statement.readable_page_ratio, 3),
        "page_coverage": round(statement.page_coverage, 3),
        "stopped_early": statement.aborted_early,
        "mean_read_confidence": round(statement.mean_read_confidence, 3),
        "required_ratio": rules.readability.min_readable_page_ratio,
        "text_layer": statement.text_layer.value,
    }

    if statement.readable_page_ratio == 0.0:
        return RuleResult(
            rule_id="R-RED-001",
            status=RuleStatus.FAIL,
            reason_code="READABILITY_NO_TEXT",
            evidence=evidence,
        )

    if statement.readable_page_ratio < rules.readability.min_readable_page_ratio:
        # `evidence_confidence` is how sure we are that the *document* is the problem.
        # A couple of bad pages we can report confidently; a document that was mostly
        # unreadable is largely guesswork, and the composite gate will route it to a
        # human rather than send the customer off with advice we do not trust.
        return RuleResult(
            rule_id="R-RED-001",
            status=RuleStatus.FAIL,
            reason_code="READABILITY_POOR_SCAN",
            evidence=evidence,
            evidence_confidence=statement.readable_page_ratio,
        )

    return RuleResult(rule_id="R-RED-001", status=RuleStatus.PASS, evidence=evidence)
