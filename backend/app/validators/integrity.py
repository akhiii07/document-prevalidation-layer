"""R-INT-001 … R-INT-004 — integrity signals.

Governing constraint (ADR-008): **these can only escalate a document to REVIEW.** They
can never reject one, and they are never described to the customer as suspicion.

Edited bank statements are a real and quantified problem in Indian lending — published
industry figures put tampering or misrepresentation at roughly 12% of statements
submitted to NBFCs — and these signals are nearly free once the PDF is already parsed.
But every one of them is individually weak: customers legitimately re-save, split and
scan their statements. So no single signal escalates anything. An automated fraud
accusation from a system with no ground truth would be both wrong and harmful.
"""

from __future__ import annotations

import logging
from typing import BinaryIO

from pypdf import PdfReader

from app.config.rules import ValidationRules
from app.domain.enums import TextLayer
from app.domain.validation import RuleResult, RuleStatus, ValidationContext

logger = logging.getLogger("docverify.integrity")


def collect_pdf_signals(stream: BinaryIO, rules: ValidationRules) -> list[RuleResult]:
    """Inspect the PDF container. Called before extraction, while the bytes are to hand."""
    signals: list[RuleResult] = []
    try:
        stream.seek(0)
        raw = stream.read()
        reader = PdfReader(stream)
        metadata = reader.metadata or {}
    except Exception as exc:  # noqa: BLE001 - a file we cannot inspect raises no signal
        logger.debug("integrity probe skipped: %s", exc)
        return signals

    producer = str(metadata.get("/Producer", "") or "")
    creator = str(metadata.get("/Creator", "") or "")
    stamp = f"{producer} {creator}"

    # R-INT-001. Note what is NOT here: a *missing* producer raises nothing. Absence of
    # metadata is not evidence of editing, and plenty of legitimate tools write none.
    matched = [p for p in rules.integrity.consumer_editor_patterns if p.lower() in stamp.lower()]
    if matched:
        signals.append(
            RuleResult(
                rule_id="R-INT-001",
                status=RuleStatus.SIGNAL,
                blocking=False,
                reason_code="INTEGRITY_SIGNALS_RAISED",
                evidence={
                    "signal": "consumer_editor_metadata",
                    "producer": producer,
                    "creator": creator,
                    "matched": matched,
                },
            )
        )

    # R-INT-002 — incremental updates. A file saved once carries a single cross-
    # reference section; several mean the document was modified after it was written.
    revisions = raw.count(b"startxref")
    if revisions > 1:
        signals.append(
            RuleResult(
                rule_id="R-INT-002",
                status=RuleStatus.SIGNAL,
                blocking=False,
                reason_code="INTEGRITY_SIGNALS_RAISED",
                evidence={"signal": "incremental_updates", "revisions": revisions},
            )
        )

    return signals


def collect_content_signals(ctx: ValidationContext, rules: ValidationRules) -> list[RuleResult]:
    """Signals visible only after extraction."""
    statement = ctx.statement
    signals: list[RuleResult] = []

    # R-INT-003 — the content arrived as an image where a bank-issued PDF would carry a
    # native text layer. Weak on its own: it correlates with scanning at least as much
    # as with editing, which is why the clean scanned statement in the corpus still
    # passes on one signal.
    if statement.text_layer is TextLayer.OCR:
        signals.append(
            RuleResult(
                rule_id="R-INT-003",
                status=RuleStatus.SIGNAL,
                blocking=False,
                reason_code="INTEGRITY_SIGNALS_RAISED",
                evidence={"signal": "image_only_document", "text_layer": "OCR"},
            )
        )

    # R-INT-004 — a calendar month inside the period with no transactions at all. Named
    # in the fraud literature, and also entirely normal for a dormant account: a signal,
    # never a finding.
    gap = _empty_month(statement)
    if gap:
        signals.append(
            RuleResult(
                rule_id="R-INT-004",
                status=RuleStatus.SIGNAL,
                blocking=False,
                reason_code="INTEGRITY_SIGNALS_RAISED",
                evidence={"signal": "month_without_transactions", **gap},
            )
        )

    return signals


def _empty_month(statement) -> dict | None:  # noqa: ANN001
    if not statement.period_start or not statement.period_end or not statement.transactions:
        return None

    months: set[tuple[int, int]] = set()
    for txn in statement.transactions:
        if txn.txn_date:
            months.add((txn.txn_date.year, txn.txn_date.month))

    expected: list[tuple[int, int]] = []
    year, month = statement.period_start.year, statement.period_start.month
    while (year, month) <= (statement.period_end.year, statement.period_end.month):
        expected.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    missing = [f"{y:04d}-{m:02d}" for y, m in expected if (y, m) not in months]
    return {"months_without_transactions": missing} if missing else None


def escalates(signals: list[RuleResult], *, balance_break: bool, rules: ValidationRules) -> bool:
    """Decide whether the signals warrant a human look.

    One weak signal never does — that would flood Operations with noise and teach
    reviewers to dismiss the queue. Either several signals agree, or a single signal
    co-occurs with a balance break, which is the combination the fraud literature
    actually describes.
    """
    if not signals:
        return False
    if len(signals) >= rules.integrity.integrity_review_threshold:
        return True
    return balance_break
