"""The validation engine — the core product phase.

Two kinds of test here. The table-driven corpus tests assert the *product contract*:
every synthetic document reaches the outcome and reason code its manifest declares. The
rest assert the invariants that make those outcomes trustworthy — above all that there
is no path from uncertainty to PASS.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.config.rules import get_rules
from app.domain.confidence import compose
from app.domain.enums import Outcome, TextLayer
from app.domain.extraction import ExtractedStatement, ExtractedTransaction
from app.domain.validation import RuleResult, RuleStatus, ValidationContext
from app.providers.extractor.native import PdfPlumberExtractor
from app.providers.llm import DeterministicLLMProvider
from app.services.messages import compose_customer_message, compose_pass_summary
from app.services.normalization import parse_statement
from app.services.validation_engine import validate
from app.validators.completeness import _balance_breaks, check_completeness
from app.validators.identity import normalise_name, similarity
from app.validators.integrity import collect_pdf_signals, escalates

RULES = get_rules()

#: OCR-backed and file-level documents are exercised elsewhere: the first are slow, the
#: second never reach the validation engine because ingestion rejects them.
NATIVE_DOCS = [
    "valid_sbi",
    "valid_hdfc",
    "valid_icici",
    "valid_axis",
    "valid_kotak",
    "wrong_period_short_hdfc",
    "wrong_period_stale_sbi",
    "wrong_document_gst_certificate",
    "incomplete_missing_pages_axis",
    "balance_break_icici",
    "wrong_account_type_savings_hdfc",
    "identity_mismatch_axis",
    "integrity_edited_producer_sbi",
]


def _verdict(path: Path, business_name: str, reference_date: date):
    raw = PdfPlumberExtractor().extract(io.BytesIO(path.read_bytes()))
    statement = parse_statement(raw, llm=DeterministicLLMProvider())
    signals = collect_pdf_signals(io.BytesIO(path.read_bytes()), RULES)
    context = ValidationContext(
        statement=statement,
        business_name=business_name,
        reference_date=reference_date,
        integrity_signals=signals,
    )
    return validate(context, RULES), statement


@pytest.fixture(scope="module")
def verdicts(request: pytest.FixtureRequest) -> dict:
    corpus_path = request.getfixturevalue("corpus_path")
    by_id = request.getfixturevalue("by_id")
    manifest = request.getfixturevalue("manifest")
    as_of = date.fromisoformat(manifest["as_of_date"])

    return {
        doc_id: _verdict(corpus_path(doc_id), by_id[doc_id]["application"]["business_name"], as_of)
        for doc_id in NATIVE_DOCS
    }


# ------------------------------------------------------------------ the contract


@pytest.mark.scenario
@pytest.mark.parametrize("doc_id", NATIVE_DOCS)
def test_every_document_reaches_its_declared_outcome(
    doc_id: str, verdicts: dict, by_id: dict
) -> None:
    verdict, _ = verdicts[doc_id]
    expected = by_id[doc_id]["expected"]

    assert verdict.outcome == expected["outcome"], (
        f"{doc_id}: got {verdict.outcome}/{verdict.primary_reason_code}, "
        f"expected {expected['outcome']}/{expected['primary_reason_code']} "
        f"(confidence {verdict.composite_confidence:.3f})"
    )
    if expected["primary_reason_code"] is not None:
        assert verdict.primary_reason_code == expected["primary_reason_code"]


@pytest.mark.parametrize("doc_id", NATIVE_DOCS)
def test_every_verdict_carries_its_evidence(doc_id: str, verdicts: dict) -> None:
    """An outcome that cannot be explained after the fact is not usable in a lending
    workflow."""
    verdict, _ = verdicts[doc_id]
    assert verdict.results, "no rule results recorded"
    assert "confidence" in verdict.evidence

    if verdict.primary_reason_code:
        firing = [r for r in verdict.results if r.reason_code == verdict.primary_reason_code]
        assert firing, "the primary reason has no rule behind it"
        assert any(r.evidence for r in firing), "the primary reason carries no evidence"


@pytest.mark.parametrize("doc_id", NATIVE_DOCS)
def test_reason_codes_are_all_in_the_catalogue(doc_id: str, verdicts: dict) -> None:
    verdict, _ = verdicts[doc_id]
    for code in filter(None, [verdict.primary_reason_code, *verdict.secondary_reason_codes]):
        assert code in RULES.known_reason_codes


# ------------------------------------------------------------------ invariants


def _statement(**overrides) -> ExtractedStatement:
    defaults = dict(
        bank_name="HDFC Bank",
        account_holder_name="SHARMA METAL WORKS PVT LTD",
        account_number="50200047183926",
        account_type="CURRENT",
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        page_count=7,
        text_layer=TextLayer.NATIVE,
        table_detected=True,
        page_coverage=1.0,
        readable_page_ratio=1.0,
        mean_read_confidence=1.0,
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 1.0),
    )
    defaults.update(overrides)
    statement = ExtractedStatement(**defaults)
    if "transactions" not in overrides:
        _fill_transactions(statement)
    return statement


def _fill_transactions(statement: ExtractedStatement, count: int = 12) -> None:
    """Chronological transactions spanning the stated period, as a real statement has.

    Both properties matter: R-CMP-004 checks that the table actually covers the period
    the header claims, and the balance chain must reconcile exactly or every test would
    be fighting a manufactured break.
    """
    assert statement.period_start and statement.period_end
    span = (statement.period_end - statement.period_start).days
    balance = 10_000_00
    for index in range(count):
        balance += 1_000_00
        statement.transactions.append(
            ExtractedTransaction(
                txn_date=statement.period_start + timedelta(days=span * index // (count - 1)),
                description=f"UPI/{index}",
                debit_paise=None,
                credit_paise=1_000_00,
                balance_paise=balance,
                page=1 + index // 6,
            )
        )


def _ctx(statement: ExtractedStatement, **kw) -> ValidationContext:
    return ValidationContext(
        statement=statement,
        business_name=kw.get("business_name", "Sharma Metal Works Private Limited"),
        reference_date=kw.get("reference_date", date(2026, 9, 12)),
        integrity_signals=kw.get("integrity_signals", []),
    )


def test_a_clean_statement_passes() -> None:
    assert validate(_ctx(_statement()), RULES).outcome == Outcome.PASS


def test_unreadable_document_never_passes() -> None:
    """The invariant the whole product rests on: uncertainty resolves to REVIEW."""
    statement = _statement(
        readable_page_ratio=0.2,
        page_coverage=0.2,
        mean_read_confidence=0.4,
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.2),
    )
    verdict = validate(_ctx(statement), RULES)
    assert verdict.outcome == Outcome.REVIEW


def test_nothing_extracted_never_passes() -> None:
    """Reading nothing at all is an actionable FIX, not a review: "send the PDF your
    bank issued rather than a photo" is a real instruction the customer can act on.

    What must never happen is PASS -- a document we could not read is not a document we
    can accept."""
    statement = ExtractedStatement(
        page_count=5, field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.0)
    )
    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome != Outcome.PASS
    assert verdict.primary_reason_code == "READABILITY_NO_TEXT"
    assert verdict.composite_confidence < RULES.confidence.pass_threshold


def test_low_confidence_downgrades_a_fix_to_review() -> None:
    """A finding may well be right, but we must be sure enough before sending a
    customer to act on it."""
    statement = _statement(
        period_start=date(2026, 5, 1),  # four months - a real coverage failure
        readable_page_ratio=0.3,
        mean_read_confidence=0.3,
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.15),
    )
    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome == Outcome.REVIEW
    assert verdict.primary_reason_code == "EXTRACTION_LOW_CONFIDENCE"
    assert verdict.evidence.get("downgraded_from") in {
        "PERIOD_INSUFFICIENT_COVERAGE",
        "READABILITY_POOR_SCAN",
    }


def test_a_wrong_document_is_still_a_fix_when_nothing_extracted() -> None:
    """The composite score measures how well we read *a bank statement*. For a document
    that is not one, that measure is meaningless and must not veto a confident,
    actionable answer (`composite_gated: false`)."""
    statement = ExtractedStatement(
        page_count=1,
        readable_page_ratio=1.0,
        page_coverage=1.0,
        mean_read_confidence=1.0,
        table_detected=False,
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.0),
    )
    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome == Outcome.FIX
    assert verdict.primary_reason_code == "DOC_TYPE_MISMATCH"


def test_readability_gates_document_type() -> None:
    """If we could not read it, we cannot claim it is the wrong document — that would
    send the customer to fetch a statement they may already have sent."""
    statement = ExtractedStatement(
        page_count=10,
        readable_page_ratio=0.1,
        table_detected=False,
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.0),
    )
    verdict = validate(_ctx(statement), RULES)

    doc_type = next(r for r in verdict.results if r.rule_id == "R-DOC-001")
    assert doc_type.status is RuleStatus.NOT_EVALUATED
    assert verdict.primary_reason_code != "DOC_TYPE_MISMATCH"


def test_missing_account_type_does_not_fail_the_document() -> None:
    """Not every Indian statement prints it. Failing on absence would tell customers to
    resend a document they already sent."""
    statement = _statement(account_type=None)
    statement.field_confidence["account_type"] = 0.0
    verdict = validate(_ctx(statement), RULES)

    rule = next(r for r in verdict.results if r.rule_id == "R-DOC-002")
    assert rule.status is RuleStatus.NOT_EVALUATED
    assert verdict.outcome == Outcome.PASS


def test_coverage_tolerance_absorbs_calendar_boundaries_only() -> None:
    """A customer who did exactly the right thing must not get a FIX because months are
    uneven — but a genuinely short statement must still fail."""
    boundary = _statement(period_start=date(2026, 3, 16), period_end=date(2026, 9, 12))
    assert validate(_ctx(boundary), RULES).outcome == Outcome.PASS

    short = _statement(period_start=date(2026, 5, 1))
    verdict = validate(_ctx(short), RULES)
    assert verdict.primary_reason_code == "PERIOD_INSUFFICIENT_COVERAGE"


def test_recency_is_measured_from_the_application_not_today() -> None:
    """Anchoring to the wall clock would make a document that validated correctly today
    start failing weeks later, through no fault of the customer."""
    statement = _statement()
    assert validate(_ctx(statement, reference_date=date(2026, 9, 12)), RULES).outcome == "PASS"

    later = validate(_ctx(statement, reference_date=date(2027, 3, 1)), RULES)
    assert later.primary_reason_code == "PERIOD_STALE"


# ------------------------------------------------------------------ completeness


def test_one_edited_balance_breaks_two_rows() -> None:
    statement = _statement()
    statement.transactions[4].balance_paise += 50_000_00

    breaks = _balance_breaks(statement.transactions, RULES.completeness.balance_tolerance_paise)
    assert [b["index"] for b in breaks] == [4, 5]


def test_balance_break_without_a_page_gap_goes_to_a_human() -> None:
    """Arithmetic cannot tell an edit from missing content, and the two need opposite
    responses. Telling the customer to re-upload would also tell a dishonest one exactly
    which figure failed (ADR-008)."""
    statement = _statement(page_numbering=[(1, 2), (2, 2)])
    statement.transactions[4].balance_paise += 50_000_00

    verdict = validate(_ctx(statement), RULES)
    assert verdict.outcome == Outcome.REVIEW
    assert verdict.primary_reason_code == "COMPLETENESS_BALANCE_BREAK"


def test_page_gap_plus_seam_break_is_an_actionable_fix() -> None:
    """Two independent signals agreeing is what turns an ambiguous anomaly into a
    confident instruction."""
    statement = _statement(page_numbering=[(1, 4), (2, 4)])
    statement.transactions[5].balance_paise += 90_000_00  # first row of page 2

    verdict = validate(_ctx(statement), RULES)
    assert verdict.outcome == Outcome.FIX
    assert verdict.primary_reason_code == "COMPLETENESS_MISSING_PAGES"

    downgraded = next(r for r in verdict.results if r.rule_id == "R-CMP-003")
    assert downgraded.status is RuleStatus.SIGNAL
    assert downgraded.evidence["explained_by"] == "COMPLETENESS_MISSING_PAGES"


def test_absent_page_markers_degrade_rather_than_fail() -> None:
    results = check_completeness(_ctx(_statement(page_numbering=[])), RULES)
    page_rule = next(r for r in results if r.rule_id == "R-CMP-001")
    assert page_rule.status is RuleStatus.NOT_EVALUATED


# ------------------------------------------------------------------ identity


@pytest.mark.parametrize(
    ("holder", "business"),
    [
        ("SHARMA METAL WORKS PVT LTD", "Sharma Metal Works Private Limited"),
        ("ACME TRADERS", "Acme Traders"),
        ("Krishna Textiles LLP", "Krishna Textiles"),
    ],
)
def test_legal_form_differences_are_not_mismatches(holder: str, business: str) -> None:
    """The same business written two ways. Without suffix stripping, every legitimate
    application would land in the review queue."""
    suffixes = RULES.identity.legal_suffixes
    score = similarity(normalise_name(holder, suffixes), normalise_name(business, suffixes))
    assert score >= RULES.identity.identity_strong_match


def test_an_individual_name_is_a_mismatch_but_only_a_review() -> None:
    """Often legitimate for a sole proprietor, which is exactly why it goes to a human
    rather than telling the customer they sent the wrong account."""
    statement = _statement(account_holder_name="RAJESH KUMAR SHARMA")
    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome == Outcome.REVIEW
    assert verdict.primary_reason_code == "IDENTITY_MISMATCH"
    assert not RULES.is_actionable("IDENTITY_MISMATCH")


def test_identity_cannot_fail_a_document_on_its_own() -> None:
    rule = next(
        r
        for r in validate(_ctx(_statement(account_holder_name="SOMEONE ELSE")), RULES).results
        if r.rule_id == "R-IDN-001"
    )
    assert rule.blocking is False


# ------------------------------------------------------------------ integrity


def test_a_single_weak_signal_does_not_escalate() -> None:
    """Otherwise Operations drowns in noise and learns to dismiss the queue."""
    one = [RuleResult(rule_id="R-INT-001", status=RuleStatus.SIGNAL, blocking=False)]
    assert escalates(one, balance_break=False, rules=RULES) is False


def test_two_signals_escalate() -> None:
    two = [
        RuleResult(rule_id="R-INT-001", status=RuleStatus.SIGNAL, blocking=False),
        RuleResult(rule_id="R-INT-003", status=RuleStatus.SIGNAL, blocking=False),
    ]
    assert escalates(two, balance_break=False, rules=RULES) is True


def test_one_signal_with_a_balance_break_escalates() -> None:
    """The combination the fraud literature actually describes."""
    one = [RuleResult(rule_id="R-INT-001", status=RuleStatus.SIGNAL, blocking=False)]
    assert escalates(one, balance_break=True, rules=RULES) is True


def test_missing_producer_metadata_is_not_a_signal(tmp_path: Path) -> None:
    """Absence of metadata is not evidence of editing — plenty of legitimate tools
    write none, and treating absence as suspicion would flag them all."""
    from pypdf import PdfWriter

    path = tmp_path / "no-metadata.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        writer.write(fh)

    assert collect_pdf_signals(io.BytesIO(path.read_bytes()), RULES) == []


def test_integrity_signals_can_never_produce_a_fix(verdicts: dict) -> None:
    """ADR-008: they raise a human check, never a verdict against the customer."""
    assert RULES.default_outcome("INTEGRITY_SIGNALS_RAISED") == "REVIEW"
    assert not RULES.is_actionable("INTEGRITY_SIGNALS_RAISED")

    verdict, _ = verdicts["integrity_edited_producer_sbi"]
    assert verdict.outcome == Outcome.PASS, "one weak signal must not disturb a good document"


# ------------------------------------------------------------------ confidence


def test_rule_coverage_drags_down_a_perfectly_read_document() -> None:
    """A document where half the rules could not run is not high-confidence however
    crisp the reading was."""
    perfect = dict.fromkeys(RULES.confidence.field_weights, 1.0)
    evaluated = [RuleResult(rule_id=f"R-{i}", status=RuleStatus.PASS) for i in range(5)]
    skipped = [RuleResult(rule_id=f"R-{i}", status=RuleStatus.NOT_EVALUATED) for i in range(5)]

    full = compose(perfect, evaluated, RULES.confidence)
    half = compose(perfect, [*evaluated, *skipped], RULES.confidence)

    assert full.composite == pytest.approx(1.0)
    assert half.composite < full.composite
    assert half.rule_coverage == pytest.approx(0.5)


def test_confidence_reports_which_fields_were_weak() -> None:
    """Operations needs to see *which* signal was weak, not just how weak the total
    was (ADR-009)."""
    fields = dict.fromkeys(RULES.confidence.field_weights, 1.0)
    fields["period_start"] = 0.2
    breakdown = compose(fields, [RuleResult(rule_id="R", status=RuleStatus.PASS)], RULES.confidence)

    assert breakdown.weakest_fields[0][0] == "period_start"


def test_signals_do_not_count_toward_rule_coverage() -> None:
    blocking = [RuleResult(rule_id="R-1", status=RuleStatus.PASS)]
    signal = RuleResult(rule_id="R-INT-001", status=RuleStatus.SIGNAL, blocking=False)
    breakdown = compose(
        dict.fromkeys(RULES.confidence.field_weights, 1.0),
        [*blocking, signal],
        RULES.confidence,
    )
    assert breakdown.rules_total == 1


# ------------------------------------------------------------------ messages


@pytest.mark.parametrize("doc_id", NATIVE_DOCS)
def test_customer_messages_never_leak_internals(doc_id: str, verdicts: dict) -> None:
    verdict, _ = verdicts[doc_id]
    message = compose_customer_message(
        Outcome(verdict.outcome), verdict.primary_reason_code, verdict.primary_evidence()
    )
    if message is None:
        return
    for leak in ("R-PER", "R-DOC", "R-CMP", "composite", "confidence", "_paise", "Traceback"):
        assert leak not in message
    if verdict.primary_reason_code:
        assert verdict.primary_reason_code not in message


def test_a_fix_message_quotes_the_actual_values(verdicts: dict) -> None:
    """Specific reason plus specific next action. "The period is wrong" is not a
    message; "01 May – 31 Aug 2026" is."""
    verdict, _ = verdicts["wrong_period_short_hdfc"]
    message = compose_customer_message(
        Outcome.FIX, verdict.primary_reason_code, verdict.primary_evidence()
    )

    assert "2026" in message
    assert "6 months" in message
    assert "Please upload" in message


def test_every_actionable_reason_has_a_message() -> None:
    """A FIX with no template would produce exactly the vague message the spec forbids."""
    from app.services.messages import _TEMPLATES

    actionable = [e.code for e in RULES.reason_codes if e.actionable]
    missing = [code for code in actionable if code not in _TEMPLATES]
    assert missing == []


def test_review_messages_never_imply_suspicion() -> None:
    message = compose_customer_message(Outcome.REVIEW, "INTEGRITY_SIGNALS_RAISED", {})
    for word in ("fraud", "suspicious", "tamper", "altered", "edited", "verify your identity"):
        assert word not in message.lower()


def test_pass_summary_names_the_document(verdicts: dict) -> None:
    _, statement = verdicts["valid_hdfc"]
    summary = compose_pass_summary(statement.to_json())

    assert "Bank statement verified" in summary
    assert "HDFC Bank" in summary
    assert "No further action is required" in summary


def test_pass_summary_never_shows_an_account_number(verdicts: dict) -> None:
    _, statement = verdicts["valid_hdfc"]
    assert "50200047183926" not in compose_pass_summary(statement.to_json())


# ------------------------------------------------- reading a document badly is not
# ------------------------------------------------- the same as the document being bad
#
# Every test below comes from one real failure: a customer sent a Canara Bank statement
# captured as phone screenshots, and the system replied that the running balance did not
# reconcile. It was specific, confident, and wrong -- the balance chain had never been
# read. These assert that each link in that chain is now broken.


def test_balance_break_needs_enough_rows_to_be_a_claim() -> None:
    """A handful of rows cannot establish that a six-month chain is broken."""
    minimum = RULES.completeness.min_transactions_for_balance_check
    statement = _statement(transactions=[])
    _fill_transactions(statement, count=minimum - 1)
    # Manufacture an unmistakable break: if the rule were evaluated it would fail.
    statement.transactions[-1].balance_paise += 99_999_00

    results = check_completeness(_ctx(statement), RULES)
    balance = next(r for r in results if r.rule_id == "R-CMP-002")

    assert balance.status is RuleStatus.NOT_EVALUATED
    assert balance.evidence["minimum_required"] == minimum


def test_balance_break_still_fires_once_there_is_enough_to_judge() -> None:
    """The guard must not become a way for a real break to escape."""
    statement = _statement(transactions=[])
    _fill_transactions(statement, count=RULES.completeness.min_transactions_for_balance_check + 2)
    statement.transactions[-1].balance_paise += 99_999_00

    results = check_completeness(_ctx(statement), RULES)
    balance = next(r for r in results if r.rule_id == "R-CMP-002")

    assert balance.status is RuleStatus.FAIL
    assert balance.reason_code == "COMPLETENESS_BALANCE_BREAK"


def test_a_cropped_screenshot_is_a_fix_not_a_review() -> None:
    """Rows we can read, columns we cannot: the customer can act on that.

    The document is legible -- dates parse, figures parse -- so "we could not read it"
    would be false. But no row carries both an amount and a balance, which is what a
    narrow screen crops away. Telling the customer to send the bank's PDF resolves it in
    one round trip; sending it to a reviewer resolves nothing.
    """
    statement = _statement(transactions=[])
    _fill_transactions(statement, count=20)
    for txn in statement.transactions:
        # The amount column never made it into the image. Everything else did.
        txn.debit_paise = None
        txn.credit_paise = None

    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome == Outcome.FIX.value
    assert verdict.primary_reason_code == "READABILITY_PARTIAL_CAPTURE"
    assert "COMPLETENESS_BALANCE_BREAK" not in verdict.secondary_reason_codes

    message = compose_customer_message(Outcome.FIX, verdict.primary_reason_code, {})
    assert message and "PDF" in message


def test_a_specific_finding_needs_the_confidence_to_claim_it() -> None:
    """The confidence gate applies to REVIEW findings, not only to FIX ones.

    Naming a finding -- "the balance does not reconcile" -- asserts we read the document
    well enough to know. On a document we barely read, that claim sends a reviewer
    hunting a defect that may not exist while hiding the real problem, which is us.
    """
    statement = _statement(
        field_confidence=dict.fromkeys(RULES.confidence.field_weights, 0.05),
        page_coverage=0.3,
        mean_read_confidence=0.4,
    )
    statement.transactions[-1].balance_paise += 50_000_00

    verdict = validate(_ctx(statement), RULES)

    assert verdict.outcome == Outcome.REVIEW.value
    assert verdict.primary_reason_code == "EXTRACTION_LOW_CONFIDENCE"
    assert verdict.evidence["downgraded_from"] == "COMPLETENESS_BALANCE_BREAK"
