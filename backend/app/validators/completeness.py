"""R-CMP-001 … R-CMP-004 — is the whole statement here?

Balance continuity is the core of this module (ADR-007). Page count proves nothing: a
statement can carry every page number and still be missing content, cropped, or
partially exported. Balance arithmetic is the one check a genuine statement cannot pass
by accident.

The four rules are evaluated together rather than independently, because their findings
*explain* one another. A page-numbering gap and a balance break at the same seam are not
two problems — they are one problem seen twice, and reporting them separately would show
the customer a confusing message and a reviewer a duplicated finding.
"""

from __future__ import annotations

from app.config.rules import ValidationRules
from app.domain.extraction import ExtractedTransaction
from app.domain.validation import RuleResult, RuleStatus, ValidationContext


def _balance_breaks(transactions: list[ExtractedTransaction], tolerance_paise: int) -> list[dict]:
    """Indices where the running balance does not carry.

    After each comparison the running total is re-synchronised to the *stated* balance.
    That is what makes the two failure shapes distinguishable: a single edited figure
    breaks the chain at its own row and the row after it, while a removed block of rows
    breaks it exactly once, at the seam.
    """
    breaks: list[dict] = []
    running: int | None = None

    for index, txn in enumerate(transactions):
        if txn.balance_paise is None:
            running = None
            continue

        if running is not None:
            expected = running + (txn.credit_paise or 0) - (txn.debit_paise or 0)
            if abs(expected - txn.balance_paise) > tolerance_paise:
                breaks.append(
                    {
                        "index": index,
                        "page": txn.page,
                        "expected_paise": expected,
                        "stated_paise": txn.balance_paise,
                        "difference_paise": txn.balance_paise - expected,
                        "txn_date": txn.txn_date.isoformat() if txn.txn_date else None,
                    }
                )
        running = txn.balance_paise

    return breaks


def _page_gap(page_numbering: list[tuple[int, int]]) -> dict | None:
    """Detect missing pages from printed "Page X of Y" markers."""
    if not page_numbering:
        return None

    seen = sorted({page for page, _ in page_numbering})
    total = max(total for _, total in page_numbering)
    missing = [n for n in range(1, total + 1) if n not in seen]
    if not missing:
        return None
    return {"pages_present": seen, "pages_total": total, "pages_missing": missing}


def check_completeness(ctx: ValidationContext, rules: ValidationRules) -> list[RuleResult]:
    statement = ctx.statement
    results: list[RuleResult] = []

    transactions = statement.transactions
    tolerance = rules.completeness.balance_tolerance_paise

    gap = _page_gap(statement.page_numbering)
    breaks = _balance_breaks(transactions, tolerance) if transactions else []
    seam_breaks = [
        b
        for b in breaks
        if b["index"] > 0 and transactions[b["index"]].page != transactions[b["index"] - 1].page
    ]

    # --- R-CMP-001: page numbering -------------------------------------------
    if not statement.page_numbering:
        # "Page X of Y" footers are common but not universal (research 3.3), so absence
        # degrades coverage rather than failing the document.
        results.append(
            RuleResult(
                rule_id="R-CMP-001",
                status=RuleStatus.NOT_EVALUATED,
                evidence={"reason": "no page-of-total markers printed"},
            )
        )
    elif gap:
        results.append(
            RuleResult(
                rule_id="R-CMP-001",
                status=RuleStatus.FAIL,
                reason_code="COMPLETENESS_MISSING_PAGES",
                evidence=gap,
            )
        )
    else:
        results.append(
            RuleResult(
                rule_id="R-CMP-001",
                status=RuleStatus.PASS,
                evidence={"pages_present": len(statement.page_numbering)},
            )
        )

    # --- R-CMP-005: was the whole table captured? ----------------------------
    results.append(_check_capture(ctx))

    # --- R-CMP-002 / R-CMP-003: balance continuity ---------------------------
    complete = statement.complete_transactions
    minimum = rules.completeness.min_transactions_for_balance_check

    if not transactions or all(t.balance_paise is None for t in transactions):
        results.append(
            RuleResult(
                rule_id="R-CMP-002",
                status=RuleStatus.NOT_EVALUATED,
                evidence={"reason": "no running balance available"},
            )
        )
    elif len(complete) < minimum:
        # Too little of the table was read to judge the chain. Breaks found in a partial
        # read are artefacts of *our* extraction, and reporting them blames the customer's
        # document for our failure to read it -- the single worst thing this product can
        # do, because it is confident, specific, and wrong.
        results.append(
            RuleResult(
                rule_id="R-CMP-002",
                status=RuleStatus.NOT_EVALUATED,
                evidence={
                    "reason": "too few complete transactions to judge the balance chain",
                    "complete_transactions": len(complete),
                    "parsed_transactions": len(transactions),
                    "minimum_required": minimum,
                },
            )
        )
    elif not breaks:
        results.append(
            RuleResult(
                rule_id="R-CMP-002",
                status=RuleStatus.PASS,
                evidence={"transactions_checked": len(transactions), "breaks": 0},
            )
        )
    else:
        evidence = {
            "transactions_checked": len(transactions),
            "break_count": len(breaks),
            # Capped: a reviewer needs the first few, not a dump of every row.
            "breaks": breaks[:3],
            "at_page_boundary": bool(seam_breaks),
        }
        if gap and seam_breaks:
            # Two independent signals agree — the page numbering shows a gap and the
            # balance fails to carry at that exact seam. Agreement is what turns an
            # ambiguous anomaly into a confident, customer-actionable instruction
            # (R-CMP-003). Reported as a SIGNAL so the missing-pages finding above is
            # the one the customer sees.
            results.append(
                RuleResult(
                    rule_id="R-CMP-003",
                    status=RuleStatus.SIGNAL,
                    blocking=False,
                    evidence={**evidence, "explained_by": "COMPLETENESS_MISSING_PAGES"},
                )
            )
        else:
            # Arithmetic alone cannot distinguish an edit from missing content, and the
            # two need opposite responses. Telling the customer to re-upload would also
            # tell a dishonest one exactly which figure failed (ADR-008).
            results.append(
                RuleResult(
                    rule_id="R-CMP-002",
                    status=RuleStatus.FAIL,
                    reason_code="COMPLETENESS_BALANCE_BREAK",
                    evidence=evidence,
                )
            )

    # --- R-CMP-004: transactions span the claimed period ---------------------
    results.append(_check_span(ctx))
    return results


def _check_capture(ctx: ValidationContext) -> RuleResult:
    """R-CMP-005 -- did the whole table make it into the file?

    Customers routinely send screenshots of their banking app rather than the PDF the
    bank issued. A phone screen shows a *narrowed* version of the table: the columns are
    there in the header, but the figures beneath them are cropped, elided, or simply not
    rendered by the app.

    The tell is specific and hard to produce any other way: a transaction table was
    found, rows *were* parsed -- so we read the dates, so the document is legible -- and
    yet not one row carries both an amount and a balance. That is not an unreadable
    document and not a defective statement. It is a complete statement, captured
    incompletely, and the customer's next action is obvious the moment you tell them.

    Naming this separately matters. Without it the same document surfaces as a balance
    break or a low-confidence review: both are true-ish, neither is useful, and the first
    one blames the customer for a defect that is not there.
    """
    statement = ctx.statement
    if not statement.table_detected or not statement.transactions:
        return RuleResult(
            rule_id="R-CMP-005",
            status=RuleStatus.NOT_EVALUATED,
            evidence={"reason": "no transaction table to assess"},
        )

    rows = statement.transactions
    with_balance = sum(1 for t in rows if t.balance_paise is not None)
    with_amount = sum(1 for t in rows if t.debit_paise or t.credit_paise)
    evidence: dict = {
        "parsed_rows": len(rows),
        "rows_with_amount": with_amount,
        "rows_with_balance": with_balance,
        "complete_rows": len(statement.complete_transactions),
        "text_layer": statement.text_layer.value,
    }

    if statement.complete_transactions:
        return RuleResult(rule_id="R-CMP-005", status=RuleStatus.PASS, evidence=evidence)

    # Which column is missing decides what we tell the customer, so record it rather
    # than leaving a reviewer to infer it from three counts.
    missing = [
        name
        for name, present in (("amount", with_amount), ("balance", with_balance))
        if not present
    ]
    return RuleResult(
        rule_id="R-CMP-005",
        status=RuleStatus.FAIL,
        reason_code="READABILITY_PARTIAL_CAPTURE",
        evidence={**evidence, "missing_columns": missing or ["amount or balance"]},
    )


def _check_span(ctx: ValidationContext) -> RuleResult:
    """Catches a header that claims six months over a table containing four.

    A period edit that the period rules alone would never see, because they only read
    what the header says.
    """
    statement = ctx.statement
    dated = [t for t in statement.transactions if t.txn_date]

    if not dated or statement.period_start is None or statement.period_end is None:
        return RuleResult(
            rule_id="R-CMP-004",
            status=RuleStatus.NOT_EVALUATED,
            evidence={"reason": "no dated transactions or no stated period"},
        )

    first, last = dated[0].txn_date, dated[-1].txn_date
    assert first is not None and last is not None
    lead_in = (first - statement.period_start).days
    trail = (statement.period_end - last).days

    evidence = {
        "first_transaction": first.isoformat(),
        "last_transaction": last.isoformat(),
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "lead_in_days": lead_in,
        "trailing_days": trail,
    }

    # A genuinely quiet account can open or close a period without activity, so the
    # tolerance is generous. This rule exists to catch a table that covers a fraction of
    # the claimed period, not to police a slow fortnight.
    quiet_tolerance_days = 45
    if lead_in > quiet_tolerance_days or trail > quiet_tolerance_days:
        return RuleResult(
            rule_id="R-CMP-004",
            status=RuleStatus.FAIL,
            reason_code="COMPLETENESS_MISSING_PAGES",
            evidence={**evidence, "tolerance_days": quiet_tolerance_days},
        )

    return RuleResult(rule_id="R-CMP-004", status=RuleStatus.PASS, evidence=evidence)
