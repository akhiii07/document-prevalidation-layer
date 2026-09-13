"""R-PER-001 … R-PER-003 — the highest-value rules in the product.

The period is the most common correctable failure and produces the clearest customer
message. Coverage and recency are kept as *separate* rules because they fail for
different reasons and need different instructions: "send the full six months" and "send
an up-to-date statement" are not interchangeable. Collapsing them into "wrong period"
would throw away exactly the information that makes the message actionable.
"""

from __future__ import annotations

from app.config.rules import ValidationRules
from app.domain.validation import RuleResult, RuleStatus, ValidationContext


def _unreadable(rule_id: str) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        status=RuleStatus.NOT_EVALUATED,
        evidence={"reason": "statement period could not be read"},
    )


def check_coverage(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    """VERIFIED — six months of current-account statements is FlexiLoans' published
    requirement, and 6–12 months is the Indian market norm."""
    statement = ctx.statement
    if statement.period_start is None or statement.period_end is None:
        return _unreadable("R-PER-001")

    covered = (statement.period_end - statement.period_start).days
    required = rules.period.required_coverage_days
    tolerance = rules.period.coverage_tolerance_days

    evidence = {
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "covered_days": covered,
        "required_days": required,
        "tolerance_days": tolerance,
        "shortfall_days": max(0, required - covered),
    }

    # The tolerance absorbs calendar-boundary effects, not genuinely short statements:
    # 1 Mar – 31 Aug is 183 days and 15 Mar – 12 Sep is 181, but a four-month statement
    # misses by roughly 60. A customer who did exactly the right thing must not get a
    # FIX because months are uneven.
    if covered >= required - tolerance:
        return RuleResult(rule_id="R-PER-001", status=RuleStatus.PASS, evidence=evidence)

    return RuleResult(
        rule_id="R-PER-001",
        status=RuleStatus.FAIL,
        reason_code="PERIOD_INSUFFICIENT_COVERAGE",
        evidence=evidence,
    )


def check_recency(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    """ASSUMPTION — the weakest-evidenced rule in the product.

    Research established the required *length* (6–12 months) but no public source
    specifies how recent the end date must be (`RESEARCH_BANK_FORMATS.md` §6). The
    tolerance is reasoning, not evidence: banks issue on monthly cycles, so the newest
    statement a customer can hold may legitimately be about a month old. This should be
    the first number calibrated against a real lender's policy.
    """
    statement = ctx.statement
    if statement.period_end is None:
        return _unreadable("R-PER-002")

    age = (ctx.reference_date - statement.period_end).days
    tolerance = rules.period.recency_tolerance_days
    evidence = {
        "period_end": statement.period_end.isoformat(),
        "reference_date": ctx.reference_date.isoformat(),
        "age_days": age,
        "tolerance_days": tolerance,
    }

    if age <= tolerance:
        return RuleResult(rule_id="R-PER-002", status=RuleStatus.PASS, evidence=evidence)

    return RuleResult(
        rule_id="R-PER-002",
        status=RuleStatus.FAIL,
        reason_code="PERIOD_STALE",
        evidence=evidence,
    )


def check_period_present(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    """R-PER-003 — never a FIX. The customer cannot fix our inability to read a date."""
    statement = ctx.statement
    if statement.period_start and statement.period_end:
        return RuleResult(
            rule_id="R-PER-003", status=RuleStatus.PASS, evidence={"source": "statement header"}
        )

    return RuleResult(
        rule_id="R-PER-003",
        status=RuleStatus.FAIL,
        reason_code="PERIOD_NOT_FOUND",
        evidence={
            "period_start": statement.period_start.isoformat() if statement.period_start else None,
            "period_end": statement.period_end.isoformat() if statement.period_end else None,
        },
        evidence_confidence=0.5,
    )
