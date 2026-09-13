"""Extraction and normalisation, measured against the corpus ground truth.

The corpus manifest records what each document actually contains, so these are accuracy
tests rather than smoke tests: every field and every transaction is compared to the
value the generator put there.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

from app.config.bank_profiles import get_reading_profile, identify_bank
from app.domain.enums import TextLayer
from app.domain.extraction import Cell, ExtractedStatement, Page, RawExtraction
from app.providers.extractor.native import PdfPlumberExtractor
from app.providers.extractor.ocr import ocr_available
from app.providers.extractor.router import extract_document
from app.providers.llm import (
    DeterministicLLMProvider,
    _parse_fields,
    get_llm,
    redact_for_llm,
)
from app.services.normalization import (
    _date_from,
    parse_amount_paise,
    parse_date,
    parse_statement,
)

BANKS = ["sbi", "hdfc", "icici", "axis", "kotak"]


def _extract(path: Path) -> ExtractedStatement:
    raw = PdfPlumberExtractor().extract(io.BytesIO(path.read_bytes()))
    return parse_statement(raw, llm=DeterministicLLMProvider())


@pytest.fixture(scope="module")
def statements(request: pytest.FixtureRequest) -> dict[str, ExtractedStatement]:
    """Parse every corpus statement once; the tests below all read from this."""
    corpus_path = request.getfixturevalue("corpus_path")
    ids = [
        *[f"valid_{b}" for b in BANKS],
        "wrong_period_short_hdfc",
        "wrong_period_stale_sbi",
        "wrong_account_type_savings_hdfc",
        "identity_mismatch_axis",
        "balance_break_icici",
        "incomplete_missing_pages_axis",
        "wrong_document_gst_certificate",
    ]
    return {doc_id: _extract(corpus_path(doc_id)) for doc_id in ids}


# ------------------------------------------------------------------ primitives


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,23,456.78", 12345678),
        ("4,85,000.00", 48500000),
        ("539.84", 53984),
        ("1,00,000", 10000000),
        ("2,40,107.20 Dr", 24010720),
        ("68,460.35 Cr", 6846035),
    ],
)
def test_indian_amounts_parse_to_exact_paise(text: str, expected: int) -> None:
    """Integer paise throughout. A float rounding error would surface later as a
    balance break -- a defect the document does not have."""
    assert parse_amount_paise(text)[0] == expected


@pytest.mark.parametrize(("text", "marker"), [("500.00 Dr", "dr"), ("500.00 Cr", "cr")])
def test_dr_cr_markers_are_read(text: str, marker: str) -> None:
    assert parse_amount_paise(text)[1] == marker


@pytest.mark.parametrize("text", ["", "N/A", "UPI/12345/SOMEONE", "--", "Balance"])
def test_non_amounts_are_rejected(text: str) -> None:
    assert parse_amount_paise(text)[0] is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("03/04/2026", date(2026, 4, 3)),
        ("03/04/26", date(2026, 4, 3)),
        ("03-04-2026", date(2026, 4, 3)),
        ("03 Apr 2026", date(2026, 4, 3)),
        ("31/08/26", date(2026, 8, 31)),
    ],
)
def test_dates_are_parsed_day_first(text: str, expected: date) -> None:
    """03/04/2026 is 3 April in India. Reading it as 4 March would produce a period
    verdict that is confidently wrong -- the worst failure available to this product."""
    assert parse_date(text, get_reading_profile()) == expected


def test_bank_identification_prefers_ifsc_over_the_printed_name() -> None:
    """IFSC is structured; a printed name can be split across cells or mangled by OCR
    (this corpus really does produce "HDFCBankLtd")."""
    assert identify_bank("some garbled text", "HDFC0000247").key == "hdfc"
    assert identify_bank("HDFCBankLtd").key == "hdfc"
    assert identify_bank("Kotak Mahindra Bank").key == "kotak"
    assert identify_bank("no bank here") is None


# ------------------------------------------------------------------ header accuracy


@pytest.mark.parametrize("bank", BANKS)
def test_header_fields_match_ground_truth(
    bank: str, statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    statement = statements[f"valid_{bank}"]
    truth = by_id[f"valid_{bank}"]["ground_truth"]

    assert statement.account_holder_name == truth["account_holder_name"]
    assert statement.masked_account_number == truth["account_number_masked"]
    assert statement.account_type == truth["account_type"]
    assert statement.period_start == date.fromisoformat(truth["period_start"])
    assert statement.period_end == date.fromisoformat(truth["period_end"])


@pytest.mark.parametrize("bank", BANKS)
def test_bank_is_identified(bank: str, statements: dict[str, ExtractedStatement]) -> None:
    assert statements[f"valid_{bank}"].bank_key == bank


def test_account_number_is_never_serialised_in_full(
    statements: dict[str, ExtractedStatement],
) -> None:
    """It is held in memory for continuity checks but masked everywhere else."""
    statement = statements["valid_hdfc"]
    assert statement.account_number == "50200047183926"
    assert "50200047183926" not in str(statement.to_json())
    assert statement.to_json()["account_number_masked"] == "XXXXXXXXXX3926"


def test_absent_account_type_is_not_invented(
    statements: dict[str, ExtractedStatement],
) -> None:
    """Kotak prints no account type. Guessing "CURRENT" would let a savings statement
    through; guessing "SAVINGS" would issue a false FIX. It must stay unknown."""
    assert statements["valid_kotak"].account_type is None
    assert statements["valid_kotak"].field_confidence["account_type"] == 0.0
    assert statements["valid_hdfc"].account_type == "CURRENT"


def test_savings_account_is_read_as_printed(
    statements: dict[str, ExtractedStatement],
) -> None:
    assert statements["wrong_account_type_savings_hdfc"].account_type == "SAVINGS"


def test_page_numbering_is_captured_where_printed(
    statements: dict[str, ExtractedStatement],
) -> None:
    assert statements["valid_hdfc"].page_numbering[0] == (1, 7)
    # Kotak prints no "Page X of Y" footer, so the completeness rule must degrade
    # rather than fail the document.
    assert statements["valid_kotak"].page_numbering == []


# ------------------------------------------------------------------ transactions


@pytest.mark.parametrize("bank", BANKS)
def test_every_transaction_is_extracted(
    bank: str, statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    statement = statements[f"valid_{bank}"]
    expected = by_id[f"valid_{bank}"]["ground_truth"]["transaction_count"]

    assert len(statement.transactions) == expected
    assert len(statement.complete_transactions) == expected, (
        "every row must yield a date, one amount and a balance -- the completeness "
        "rules cannot run on partial rows"
    )


@pytest.mark.parametrize("bank", BANKS)
def test_extracted_balances_reconcile_exactly(
    bank: str, statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    """The property ADR-007 rests on.

    If extraction were even slightly lossy, the balance-continuity validator would
    report breaks on perfectly good documents -- and the product's most useful
    completeness signal would be worthless.
    """
    statement = statements[f"valid_{bank}"]
    truth = by_id[f"valid_{bank}"]["ground_truth"]

    balance = truth["opening_balance_paise"]
    for index, txn in enumerate(statement.transactions):
        balance += (txn.credit_paise or 0) - (txn.debit_paise or 0)
        assert balance == txn.balance_paise, f"{bank}: balance diverges at row {index}"

    assert balance == truth["closing_balance_paise"]


def test_multiline_narration_is_joined_not_split(
    statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    """Kotak wraps narration onto a second line. Research calls this the single biggest
    extraction hazard: rows stop mapping one-to-one onto transactions, and a line-based
    parser silently emits phantom rows or drops real ones."""
    statement = statements["valid_kotak"]
    assert len(statement.transactions) == by_id["valid_kotak"]["ground_truth"]["transaction_count"]

    wrapped = [t for t in statement.transactions if len(t.description) > 80]
    assert wrapped, "the wrapped narration was not reassembled"
    assert all(t.txn_date is not None for t in statement.transactions)


def test_single_amount_column_with_dr_cr_marker(
    statements: dict[str, ExtractedStatement],
) -> None:
    """ICICI uses one amount column plus a Dr/Cr marker; a naive two-column parser
    reads every transaction with the wrong sign."""
    statement = statements["valid_icici"]
    assert any(t.credit_paise for t in statement.transactions)
    assert any(t.debit_paise for t in statement.transactions)
    assert all(bool(t.debit_paise) != bool(t.credit_paise) for t in statement.complete_transactions)


def test_separate_debit_and_credit_columns(
    statements: dict[str, ExtractedStatement],
) -> None:
    statement = statements["valid_axis"]
    assert any(t.debit_paise for t in statement.transactions)
    assert any(t.credit_paise for t in statement.transactions)


# ------------------------------------------------------------------ defect documents


def test_edited_balance_shows_as_a_two_row_discontinuity(
    statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    """One altered figure breaks the chain at the edited row and the row after it."""
    statement = statements["balance_break_icici"]
    truth = by_id["balance_break_icici"]["ground_truth"]

    breaks = []
    balance = truth["opening_balance_paise"]
    for index, txn in enumerate(statement.transactions):
        balance += (txn.credit_paise or 0) - (txn.debit_paise or 0)
        if txn.balance_paise is None or balance != txn.balance_paise:
            breaks.append(index)
        balance = txn.balance_paise if txn.balance_paise is not None else balance

    assert len(breaks) == 2
    assert breaks[1] == breaks[0] + 1


def test_missing_pages_show_as_one_seam_and_a_numbering_gap(
    statements: dict[str, ExtractedStatement], by_id: dict
) -> None:
    """Two independent signals agree, which is what lets R-CMP-003 issue a confident,
    customer-actionable FIX rather than routing to a human."""
    statement = statements["incomplete_missing_pages_axis"]
    truth = by_id["incomplete_missing_pages_axis"]["ground_truth"]

    assert len(statement.transactions) < truth["transaction_count"]

    breaks = []
    balance = truth["opening_balance_paise"]
    for index, txn in enumerate(statement.transactions):
        balance += (txn.credit_paise or 0) - (txn.debit_paise or 0)
        if balance != txn.balance_paise:
            breaks.append(index)
        balance = txn.balance_paise
    assert len(breaks) == 1, "removing a contiguous block leaves exactly one seam"

    printed = [total for _, total in statement.page_numbering]
    assert printed and len(statement.page_numbering) < printed[0]


def test_short_period_is_read_as_short(statements: dict[str, ExtractedStatement]) -> None:
    statement = statements["wrong_period_short_hdfc"]
    assert statement.period_start and statement.period_end
    assert (statement.period_end - statement.period_start).days < 180


def test_stale_period_is_read_with_full_coverage(
    statements: dict[str, ExtractedStatement],
) -> None:
    statement = statements["wrong_period_stale_sbi"]
    assert (statement.period_end - statement.period_start).days >= 180


def test_gst_certificate_yields_no_statement_structure(
    statements: dict[str, ExtractedStatement],
) -> None:
    """The wrong-document case. No transaction table, no period, no account type --
    which is what document-type classification will key on in Phase 7."""
    statement = statements["wrong_document_gst_certificate"]
    assert statement.transactions == []
    assert statement.period_start is None
    assert statement.account_type is None
    assert statement.field_confidence["transactions"] == 0.0


def test_identity_mismatch_document_reads_the_individual_name(
    statements: dict[str, ExtractedStatement],
) -> None:
    assert statements["identity_mismatch_axis"].account_holder_name == "RAJESH KUMAR SHARMA"


# ------------------------------------------------------------------ confidence


@pytest.mark.parametrize("bank", BANKS)
def test_native_text_extraction_is_full_confidence(
    bank: str, statements: dict[str, ExtractedStatement]
) -> None:
    """A native text layer is exact, so anything below 1.0 would misrepresent it."""
    statement = statements[f"valid_{bank}"]
    assert statement.text_layer is TextLayer.NATIVE
    assert statement.field_confidence["transactions"] == 1.0
    assert statement.field_confidence["period_start"] == 1.0


def test_confidence_covers_every_weighted_field(
    statements: dict[str, ExtractedStatement],
) -> None:
    """A field with no confidence entry would silently contribute zero to the weighted
    score without anyone noticing it was never read."""
    from app.config.rules import get_rules

    weighted = set(get_rules().confidence.field_weights)
    for statement in statements.values():
        assert weighted <= set(statement.field_confidence)


def test_empty_extraction_scores_zero_not_one() -> None:
    """The dangerous failure: nothing extracted, but scored as confident."""
    statement = parse_statement(
        RawExtraction(provider="test", text_layer=TextLayer.NONE, document_page_count=3),
        llm=DeterministicLLMProvider(),
    )
    assert all(v == 0.0 for v in statement.field_confidence.values())
    assert "no pages could be read" in statement.warnings


# ------------------------------------------------------------------ routing


def test_native_documents_route_to_the_text_extractor(corpus_path) -> None:
    raw = extract_document(io.BytesIO(corpus_path("valid_hdfc").read_bytes()))
    assert raw.provider == "pdfplumber"
    assert raw.text_layer is TextLayer.NATIVE


def test_image_only_documents_do_not_route_to_the_text_extractor(corpus_path) -> None:
    """A scanned PDF has no usable text layer; treating it as native would yield an
    empty extraction that then looks like an empty document."""
    assert not PdfPlumberExtractor().supports(
        io.BytesIO(corpus_path("scanned_clean_hdfc").read_bytes())
    )


# ------------------------------------------------------------------ LLM boundary


def test_no_api_key_means_the_deterministic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-010: the system must be fully functional with the LLM switched off."""
    assert get_llm().name == "deterministic"


def test_deterministic_provider_invents_nothing() -> None:
    """A missing field lowers confidence and pushes toward REVIEW. Filling the gap with
    a guess would produce a confident verdict from data that was never read."""
    assert DeterministicLLMProvider().extract_header_fields("any header", ["period_start"]) == {}


def test_llm_input_is_redacted_before_it_leaves_the_process() -> None:
    """Data minimisation: an LLM call is foreign processing unless served in-region
    (`RESEARCH_REGULATORY.md` section 5)."""
    redacted = redact_for_llm("Account No 50200047183926 Name ACME TRADERS")
    assert "50200047183926" not in redacted
    assert "3926" in redacted
    assert "ACME TRADERS" in redacted


def test_llm_input_is_truncated() -> None:
    assert len(redact_for_llm("x" * 10_000)) <= 2000


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "```json\n{broken\n```",
        "[1, 2, 3]",
        "",
    ],
)
def test_unparseable_llm_output_is_discarded(payload: str) -> None:
    assert _parse_fields(payload, ["account_type"]) == {}


def test_llm_output_is_filtered_to_requested_fields() -> None:
    """The pipeline must not be able to acquire a field it did not ask for: every
    field feeds a rule."""
    result = _parse_fields(
        '{"account_type": "CURRENT", "account_number": "123456789", "verdict": "PASS"}',
        ["account_type"],
    )
    assert result == {"account_type": "CURRENT"}


def test_llm_fenced_json_is_recovered() -> None:
    assert _parse_fields('```json\n{"account_type": "CURRENT"}\n```', ["account_type"]) == {
        "account_type": "CURRENT"
    }


def test_llm_assist_is_recorded_for_audit() -> None:
    """A reviewer must be able to tell a directly-read field from an assisted one."""

    class FakeLLM:
        name = "fake"
        available = True

        def extract_header_fields(self, header_text: str, fields: list[str]) -> dict[str, str]:
            return {"account_type": "CURRENT"}

    page = Page(
        number=1,
        width=595,
        height=842,
        cells=[Cell(text="Some Bank Ltd", x0=0, x1=100, top=10, bottom=20)],
    )
    statement = parse_statement(
        RawExtraction(
            provider="test", text_layer=TextLayer.NATIVE, pages=[page], document_page_count=1
        ),
        llm=FakeLLM(),
    )

    assert statement.account_type == "CURRENT"
    assert "account_type" in statement.llm_assisted_fields
    assert statement.field_confidence["account_type"] == 0.70, (
        "an assisted read is worth less than a direct one and must never carry a verdict"
    )


def test_llm_cannot_supply_an_account_number() -> None:
    """Not in the assistable set, and never sent to the model at all."""
    from app.providers.llm import ASSISTABLE_FIELDS

    assert "account_number" not in ASSISTABLE_FIELDS


# ------------------------------------------------------------------ OCR


@pytest.mark.skipif(not ocr_available(), reason="OCR engine not installed")
@pytest.mark.slow
def test_ocr_reads_a_clean_scan(corpus_path) -> None:
    raw = extract_document(io.BytesIO(corpus_path("scanned_clean_hdfc").read_bytes()))
    statement = parse_statement(raw, llm=DeterministicLLMProvider())

    assert raw.text_layer is TextLayer.OCR
    assert statement.account_holder_name is not None
    assert statement.period_start is not None
    assert len(statement.transactions) > 100


@pytest.mark.skipif(not ocr_available(), reason="OCR engine not installed")
@pytest.mark.slow
def test_degraded_scan_scores_lower_than_a_clean_one(corpus_path) -> None:
    """The whole basis of the low-confidence scenario: confidence must track image
    quality, not merely the absence of a text layer."""
    clean = extract_document(io.BytesIO(corpus_path("scanned_clean_hdfc").read_bytes()))
    poor = extract_document(io.BytesIO(corpus_path("scanned_poor_kotak").read_bytes()))

    assert poor.mean_confidence < clean.mean_confidence
    assert poor.aborted_early, "a document we already know we cannot trust must not be finished"
    assert poor.page_coverage < 1.0


# --------------------------------------------- a column we do not recognise must not
# --------------------------------------------- become a column we do
#
# From a real failure. A Canara Bank statement carries a "Dr/Cr" column we have no
# synonym for. Its header position was ignored, so the gap it left was absorbed by the
# date column next to it, every date read as "24/04/2025 Dr", every date failed to
# parse, and every row of the statement was discarded as a continuation line. The
# document then looked empty, which the validator reported as a defect in the document.


def _page_from(rows: list[list[tuple[str, float, float]]]) -> Page:
    """Build a page from (text, x0, x1) triples, one list per visual row."""
    cells = [
        Cell(text=text, x0=x0, x1=x1, top=20.0 * index, bottom=20.0 * index + 12, confidence=1.0)
        for index, row in enumerate(rows)
        for text, x0, x1 in row
    ]
    return Page(number=1, width=1240.0, height=1754.0, cells=cells)


#: Header and row geometry taken from the statement that exposed this.
_UNKNOWN_COLUMN_PAGE = [
    [("DATE", 215, 285), ("Dr/Cr", 357, 434), ("AMOUNT", 534, 656), ("BALANCE", 907, 1026)],
    [("24/04/2025", 213, 340), ("Dr", 372, 409), ("1,200.00", 760, 864)],
    [("21/04/2025", 212, 341), ("Cr", 374, 406), ("1,470.00", 759, 865)],
]


def test_an_unrecognised_column_does_not_swallow_its_neighbour() -> None:
    raw = RawExtraction(
        provider="test",
        text_layer=TextLayer.OCR,
        pages=[_page_from(_UNKNOWN_COLUMN_PAGE)],
        document_page_count=1,
    )
    statement = parse_statement(raw, llm=DeterministicLLMProvider())

    assert statement.table_detected
    assert [t.txn_date for t in statement.transactions] == [
        date(2025, 4, 24),
        date(2025, 4, 21),
    ], "the Dr/Cr marker was read as part of the date and destroyed the row"


def test_a_stray_token_in_the_date_column_does_not_cost_the_row() -> None:
    """Belt and braces for the same failure: even if a marker does land in the date
    column, one unparseable token must not discard a transaction."""
    profile = get_reading_profile()
    cells = [
        Cell(text="24/04/2025", x0=0, x1=10, top=0, bottom=10, confidence=1.0),
        Cell(text="Dr", x0=12, x1=18, top=0, bottom=10, confidence=1.0),
    ]
    assert _date_from(cells, profile) == date(2025, 4, 24)


def test_a_bank_is_identified_from_the_masthead_not_from_a_narration() -> None:
    """UPI narrations name the *counterparty's* bank. On a busy account they outnumber
    the issuer's own name many times over."""
    page = _page_from(
        [
            [("Canara Bank", 469, 666)],
            [("DATE", 215, 285), ("AMOUNT", 534, 656), ("BALANCE", 907, 1026)],
            [("24/04/2025", 213, 340), ("UPI/DR/NANDKUMAR/HDFC BANK/", 400, 700)],
        ]
    )
    raw = RawExtraction(
        provider="test", text_layer=TextLayer.OCR, pages=[page], document_page_count=1
    )
    statement = parse_statement(raw, llm=DeterministicLLMProvider())

    assert statement.bank_name == "Canara Bank"
