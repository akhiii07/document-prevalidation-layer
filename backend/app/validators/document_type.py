"""R-DOC-001 / R-DOC-002 — is this the document we asked for?"""

from __future__ import annotations

from app.config.rules import ValidationRules
from app.domain.validation import RuleResult, RuleStatus, ValidationContext

#: Weighted structural evidence that a document is a bank statement.
#:
#: Structural, not textual, and for a concrete reason: the GST certificate in the corpus
#: contains the word "Particulars" — inside "Particulars of Approving Authority" — and
#: "Particulars" is also an Axis Bank transaction-column label. A classifier keyed on
#: terms would call a GST certificate a bank statement.
_SIGNALS: tuple[tuple[str, float], ...] = (
    ("table_detected", 0.40),
    ("has_transactions", 0.30),
    ("has_period", 0.15),
    ("has_account_number", 0.15),
)


def classify_document(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    statement = ctx.statement

    observed = {
        "table_detected": statement.table_detected,
        "has_transactions": len(statement.transactions) >= 5,
        "has_period": statement.period_start is not None and statement.period_end is not None,
        "has_account_number": statement.account_number is not None,
    }
    score = sum(weight for name, weight in _SIGNALS if observed[name])
    evidence = {
        **observed,
        "score": round(score, 3),
        "threshold": rules.document.doc_type_confidence_threshold,
        "transaction_count": len(statement.transactions),
    }

    # The readability gate. If we could not read the document, we cannot claim it is the
    # *wrong* document — and saying so would send the customer to fetch a statement they
    # may well have sent already.
    if statement.readable_page_ratio < rules.readability.min_readable_page_ratio:
        return RuleResult(
            rule_id="R-DOC-001",
            status=RuleStatus.NOT_EVALUATED,
            evidence={**evidence, "skipped": "document could not be read reliably"},
        )

    if score >= rules.document.doc_type_confidence_threshold:
        return RuleResult(rule_id="R-DOC-001", status=RuleStatus.PASS, evidence=evidence)

    # A readable document with no transaction table and no statement period is
    # confidently not a bank statement. Falling one weak signal short of the threshold
    # is not the same thing, and must not produce the same message.
    if not observed["table_detected"] and not observed["has_period"]:
        return RuleResult(
            rule_id="R-DOC-001",
            status=RuleStatus.FAIL,
            reason_code="DOC_TYPE_MISMATCH",
            evidence=evidence,
        )

    return RuleResult(
        rule_id="R-DOC-001",
        status=RuleStatus.FAIL,
        reason_code="DOC_TYPE_UNCERTAIN",
        evidence=evidence,
        evidence_confidence=score,
    )


def check_account_type(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    """The requirement is specifically a *current* account — VERIFIED from FlexiLoans'
    published document list."""
    statement = ctx.statement
    printed = (statement.account_type or "").strip()

    if not printed:
        # Not every Indian statement prints the account type (research 3.1). Failing on
        # absence would tell customers to resend a document they already sent, so this
        # degrades rule coverage instead of failing the document.
        return RuleResult(
            rule_id="R-DOC-002",
            status=RuleStatus.NOT_EVALUATED,
            evidence={"account_type": None, "reason": "not printed on this statement"},
        )

    normalised = printed.lower().replace("-", " ").strip()
    evidence = {"account_type": printed, "accepted": rules.document.current_account_tokens}

    if any(token in normalised for token in rules.document.current_account_tokens):
        return RuleResult(rule_id="R-DOC-002", status=RuleStatus.PASS, evidence=evidence)

    return RuleResult(
        rule_id="R-DOC-002",
        status=RuleStatus.FAIL,
        reason_code="ACCOUNT_TYPE_NOT_CURRENT",
        evidence=evidence,
    )
