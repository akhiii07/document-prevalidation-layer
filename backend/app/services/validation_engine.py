"""The validation engine.

Runs every rule, composes confidence, and resolves a single outcome. This is the whole
of the decision path: no other code in the system produces a verdict, and nothing in
here consults an LLM (ADR-001).

Resolution follows `VALIDATION_RULES.md` §8.5, with one invariant asserted in code
rather than merely documented: **there is no path from uncertainty to PASS.**
"""

from __future__ import annotations

import logging

from app.config.rules import ValidationRules, get_rules
from app.domain.confidence import ConfidenceBreakdown, compose
from app.domain.enums import Outcome
from app.domain.validation import RuleResult, RuleStatus, ValidationContext, Verdict
from app.validators.completeness import check_completeness
from app.validators.document_type import check_account_type, classify_document
from app.validators.identity import check_identity
from app.validators.integrity import collect_content_signals, escalates
from app.validators.period import check_coverage, check_period_present, check_recency
from app.validators.readability import check_readability

logger = logging.getLogger("docverify.validation")


def run_rules(ctx: ValidationContext, rules: ValidationRules) -> list[RuleResult]:
    """Evaluate every rule.

    Order matters in one place only: readability runs first, because document-type
    classification is gated on it. Everything else is independent.
    """
    results: list[RuleResult] = [check_readability(ctx, rules)]
    results.append(classify_document(ctx, rules))
    results.append(check_account_type(ctx, rules))
    results.append(check_period_present(ctx, rules))
    results.append(check_coverage(ctx, rules))
    results.append(check_recency(ctx, rules))
    results.extend(check_completeness(ctx, rules))
    results.append(check_identity(ctx, rules))
    results.extend(ctx.integrity_signals)
    results.extend(collect_content_signals(ctx, rules))
    return results


def validate(ctx: ValidationContext, rules: ValidationRules | None = None) -> Verdict:
    rules = rules or get_rules()
    results = run_rules(ctx, rules)
    confidence = compose(ctx.statement.field_confidence, results, rules.confidence)
    return _resolve(results, confidence, rules)


def _resolve(
    results: list[RuleResult], confidence: ConfidenceBreakdown, rules: ValidationRules
) -> Verdict:
    failures = [r for r in results if r.failed and r.reason_code]
    blocking_failures = sorted(
        (r for r in failures if r.blocking),
        key=lambda r: rules.reason_rank(r.reason_code or ""),
    )
    advisory_failures = sorted(
        (r for r in failures if not r.blocking),
        key=lambda r: rules.reason_rank(r.reason_code or ""),
    )
    signals = [r for r in results if r.status is RuleStatus.SIGNAL and r.reason_code]

    def build(outcome: Outcome, primary: str | None, extra: dict | None = None) -> Verdict:
        secondary = [
            r.reason_code
            for r in [*blocking_failures, *advisory_failures]
            if r.reason_code and r.reason_code != primary
        ]
        return Verdict(
            outcome=outcome.value,
            primary_reason_code=primary,
            secondary_reason_codes=list(dict.fromkeys(secondary)),
            results=results,
            composite_confidence=confidence.composite,
            field_score=confidence.field_score,
            rule_coverage=confidence.rule_coverage,
            evidence={"confidence": confidence.to_json(), **(extra or {})},
        )

    # --- a blocking rule failed ---------------------------------------------
    if blocking_failures:
        primary = blocking_failures[0]
        code = primary.reason_code or "EXTRACTION_LOW_CONFIDENCE"

        confident_enough = (
            not rules.is_composite_gated(code)
            or confidence.composite >= rules.confidence.fix_threshold
        )
        evidence_solid = primary.evidence_confidence >= rules.confidence.rule_evidence_threshold

        # The confidence gate applies to REVIEW findings as well, not only to FIX.
        #
        # Reporting a *specific* finding -- "the balance does not reconcile" -- asserts
        # that we read the document well enough to know that. On a document we barely
        # read, that is a confident claim built on nothing, and it sends a reviewer
        # looking for a defect that may not exist while hiding the real problem, which
        # is that extraction failed. Naming the finding is a claim; the claim needs the
        # same evidence as any other.
        if not (confident_enough and evidence_solid):
            logger.info(
                "downgrading %s to low confidence: composite=%.3f evidence=%.3f",
                code,
                confidence.composite,
                primary.evidence_confidence,
            )
            return build(
                Outcome.REVIEW, "EXTRACTION_LOW_CONFIDENCE", {"downgraded_from": code}
            )

        if rules.default_outcome(code) == "REVIEW" or not rules.is_actionable(code):
            return build(Outcome.REVIEW, code)

        return build(Outcome.FIX, code)

    # --- integrity signals ---------------------------------------------------
    balance_break = any(
        r.reason_code == "COMPLETENESS_BALANCE_BREAK" for r in [*failures, *signals]
    )
    if escalates(signals, balance_break=balance_break, rules=rules):
        return build(
            Outcome.REVIEW,
            "INTEGRITY_SIGNALS_RAISED",
            {"signals": [r.evidence.get("signal") for r in signals]},
        )

    # --- advisory failures (identity) ----------------------------------------
    if advisory_failures:
        return build(Outcome.REVIEW, advisory_failures[0].reason_code)

    # --- nothing failed: is the reading good enough to say so? ---------------
    if confidence.composite >= rules.confidence.pass_threshold:
        return build(Outcome.PASS, None)

    # Nothing failed, but not enough of the document was legible to claim it is fine.
    # This is the branch that makes "uncertainty never becomes PASS" true.
    return build(Outcome.REVIEW, "EXTRACTION_LOW_CONFIDENCE")
