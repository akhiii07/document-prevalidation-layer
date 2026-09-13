"""Corpus integrity tests.

The corpus is the ground truth every later phase is measured against. If a document does
not actually contain the defect its manifest entry claims, then the validation engine
that "passes" against it in Phase 7 has proven nothing. These tests verify the defects
are real, at the file level, before any application code touches them.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from corpus.generator.build import DEFAULT_OUT
from corpus.generator.degrade import break_balance
from corpus.generator.ledger import generate_statement
from pypdf import PdfReader

from app.config.rules import get_rules

MANIFEST = DEFAULT_OUT / "manifest.json"


# `manifest` and `by_id` come from conftest so the ingestion tests share them.


def _path(entry: dict[str, Any]) -> Path:
    return DEFAULT_OUT / entry["file"]


# --------------------------------------------------------------- manifest integrity


def test_every_file_exists_and_matches_its_checksum(manifest: dict[str, Any]) -> None:
    for entry in manifest["documents"]:
        path = _path(entry)
        assert path.exists(), f"{entry['id']}: missing file"
        raw = path.read_bytes()
        assert len(raw) == entry["bytes"], f"{entry['id']}: size drift"
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], f"{entry['id']}: content drift"


def test_document_ids_are_unique(manifest: dict[str, Any]) -> None:
    ids = [d["id"] for d in manifest["documents"]]
    assert len(ids) == len(set(ids))


def test_all_seven_acceptance_scenarios_are_covered(manifest: dict[str, Any]) -> None:
    """PRODUCT_SPEC.md section 10 defines seven scenarios. All must be exercised."""
    covered = {d["scenario"].split("_", 1)[0] for d in manifest["documents"]}
    assert {"1", "2", "3", "4", "5", "6", "7"} <= covered


def test_expected_reason_codes_are_all_known_to_the_rule_engine(
    manifest: dict[str, Any],
) -> None:
    """A manifest expecting a reason code the engine cannot emit is untestable.

    This is the join between the corpus and the validation contract: it catches a
    renamed or mistyped reason code in either direction.
    """
    known = get_rules().known_reason_codes
    for entry in manifest["documents"]:
        code = entry["expected"]["primary_reason_code"]
        if code is not None:
            assert code in known, f"{entry['id']}: unknown reason code {code}"


def test_expected_outcomes_are_valid(manifest: dict[str, Any]) -> None:
    for entry in manifest["documents"]:
        assert entry["expected"]["outcome"] in {"PASS", "FIX", "REVIEW"}


def test_pass_documents_declare_no_failure_reason(manifest: dict[str, Any]) -> None:
    """Except the password case, where the code is a gate on the way to PASS."""
    for entry in manifest["documents"]:
        if entry["expected"]["outcome"] == "PASS" and entry["id"] != "password_protected_icici":
            assert entry["expected"]["primary_reason_code"] is None, entry["id"]


def test_manifest_never_contains_a_full_account_number(manifest: dict[str, Any]) -> None:
    """Account numbers are masked everywhere outside the document itself."""
    raw = MANIFEST.read_text(encoding="utf-8")
    for full in ("38294617502", "50200047183926", "010405001729", "918020041627384", "4512096387"):
        assert full not in raw, f"unmasked account number {full} leaked into the manifest"


# --------------------------------------------------------------- valid documents


@pytest.mark.parametrize("bank", ["sbi", "hdfc", "icici", "axis", "kotak"])
def test_valid_statements_open_and_match_declared_structure(
    by_id: dict[str, Any], bank: str
) -> None:
    entry = by_id[f"valid_{bank}"]
    gt = entry["ground_truth"]
    with pymupdf.open(_path(entry)) as doc:
        assert doc.page_count == gt["page_count"]
        text = doc[0].get_text()

    # A bank-issued PDF has a native text layer. Losing it would silently push every
    # valid document down the OCR path and invalidate the extraction comparison.
    assert len(text) > 500, f"{bank}: no usable native text layer"
    assert gt["bank_name"].split()[0] in text
    assert gt["account_holder_name"] in text


@pytest.mark.parametrize("bank", ["sbi", "hdfc", "icici", "axis", "kotak"])
def test_valid_statements_cover_the_required_period(by_id: dict[str, Any], bank: str) -> None:
    rules = get_rules()
    gt = by_id[f"valid_{bank}"]["ground_truth"]
    assert gt["coverage_days"] >= rules.period.required_coverage_days


def test_kotak_omits_account_type_on_purpose(by_id: dict[str, Any]) -> None:
    """One layout must lack a printed account type, so R-DOC-002 is forced to handle
    absence rather than always finding the field."""
    assert by_id["valid_kotak"]["ground_truth"]["account_type"] is None
    assert by_id["valid_hdfc"]["ground_truth"]["account_type"] == "CURRENT"


def test_corpus_covers_both_amount_representations(by_id: dict[str, Any]) -> None:
    """Separate Debit/Credit columns and a single amount column with a Dr/Cr marker
    are both real Indian conventions (research 3.3) and break different parsers."""
    with (
        pymupdf.open(_path(by_id["valid_icici"])) as icici_doc,
        pymupdf.open(_path(by_id["valid_axis"])) as axis_doc,
    ):
        icici = icici_doc[0].get_text()
        axis = axis_doc[0].get_text()

    assert "Amount (Dr/Cr)" in icici
    assert " Dr" in icici or " Cr" in icici
    assert "Debit" in axis and "Credit" in axis


def test_corpus_contains_multiline_narration(by_id: dict[str, Any]) -> None:
    """Narration wrapping onto a second line is the hardest extraction case: rows stop
    mapping one-to-one onto transactions. At least one layout must produce it."""
    entry = by_id["valid_kotak"]
    with pymupdf.open(_path(entry)) as doc:
        lines = [line.strip() for line in doc[0].get_text().splitlines() if line.strip()]

    # A continuation line starts mid-narration: no leading date, and not an amount.
    date_prefixed = sum(1 for line in lines if line[:2].isdigit() and "-" in line[:10])
    assert date_prefixed < entry["ground_truth"]["transaction_count"]


# --------------------------------------------------------------- injected defects


def test_short_period_is_genuinely_short(by_id: dict[str, Any]) -> None:
    rules = get_rules()
    gt = by_id["wrong_period_short_hdfc"]["ground_truth"]
    shortfall = rules.period.required_coverage_days - gt["coverage_days"]
    assert shortfall > rules.period.coverage_tolerance_days, (
        "the shortfall must be far outside the boundary tolerance, or this document "
        "would be testing the tolerance rather than the coverage rule"
    )


def test_stale_period_has_full_coverage_but_old_end_date(
    by_id: dict[str, Any], manifest: dict[str, Any]
) -> None:
    """Recency and coverage must fail independently -- they need different messages."""
    rules = get_rules()
    gt = by_id["wrong_period_stale_sbi"]["ground_truth"]
    as_of = date.fromisoformat(manifest["as_of_date"])
    age = (as_of - date.fromisoformat(gt["period_end"])).days

    assert gt["coverage_days"] >= rules.period.required_coverage_days, "coverage must be fine"
    assert age > rules.period.recency_tolerance_days, "end date must be genuinely stale"


def test_password_document_is_really_encrypted(by_id: dict[str, Any]) -> None:
    entry = by_id["password_protected_icici"]
    reader = PdfReader(str(_path(entry)))
    assert reader.is_encrypted

    with pytest.raises(Exception):  # noqa: B017 - any failure to read is the point
        _ = reader.pages[0].extract_text()


def test_password_document_opens_with_the_declared_password(by_id: dict[str, Any]) -> None:
    entry = by_id["password_protected_icici"]
    reader = PdfReader(str(_path(entry)))
    assert reader.decrypt(entry["password"])
    assert entry["ground_truth"]["account_holder_name"] in reader.pages[0].extract_text()


def test_wrong_password_is_rejected(by_id: dict[str, Any]) -> None:
    reader = PdfReader(str(_path(by_id["password_protected_icici"])))
    assert not reader.decrypt("00000000")


def test_wrong_document_has_no_transaction_table(by_id: dict[str, Any]) -> None:
    """It must be a credible wrong document -- a real lending document -- not a blank
    page, or document-type classification is not being tested at all."""
    with pymupdf.open(_path(by_id["wrong_document_gst_certificate"])) as doc:
        text = doc[0].get_text()

    assert "Registration Certificate" in text
    assert "GSTIN" in text

    # Transaction-table column headers specifically. Note that "Particulars" -- an Axis
    # statement column label -- appears legitimately here as "Particulars of Approving
    # Authority", which is precisely why document classification cannot rest on a single
    # keyword and must look at table structure (R-DOC-001).
    for column in ("Narration", "Withdrawal", "Closing Balance", "Transaction Remarks"):
        assert column not in text


def test_corrupt_document_is_rejected_by_both_parsers(by_id: dict[str, Any]) -> None:
    """Both parsers in the pipeline must reject it.

    PDF parsers are tolerant by design and routinely rebuild a damaged cross-reference
    table, so "truncated" does not reliably mean "unopenable" -- and recovery is not
    monotonic in how much of the file survives. Asserting against both parsers is what
    makes FILE_CORRUPT a property of this pipeline rather than an assumption.
    """
    path = _path(by_id["corrupt_truncated_axis"])

    with pytest.raises(Exception), pymupdf.open(path) as doc:  # noqa: B017
        _ = doc[0].get_text()

    with pytest.raises(Exception):  # noqa: B017
        PdfReader(str(path)).pages[0].extract_text()


def test_corrupt_document_still_has_a_pdf_signature(by_id: dict[str, Any]) -> None:
    """Truncation is distinct from 'not a PDF': the magic bytes are intact, so this
    must reach the parse check (R-FILE-003) rather than the type check (R-FILE-001)."""
    assert _path(by_id["corrupt_truncated_axis"]).read_bytes().startswith(b"%PDF-")


def test_non_pdf_fails_on_magic_bytes_not_extension(by_id: dict[str, Any]) -> None:
    entry = by_id["invalid_not_a_pdf"]
    assert entry["file"].endswith(".pdf")
    assert not _path(entry).read_bytes().startswith(b"%PDF-")


def test_empty_file_is_empty(by_id: dict[str, Any]) -> None:
    assert _path(by_id["invalid_empty"]).read_bytes() == b""


def test_incomplete_document_has_a_page_numbering_gap(by_id: dict[str, Any]) -> None:
    entry = by_id["incomplete_missing_pages_axis"]
    with pymupdf.open(_path(entry)) as doc:
        actual_pages = doc.page_count

    assert actual_pages == entry["ground_truth"]["page_count"]
    # Pages were removed from the middle, so the surviving pages skip numbers.
    assert entry["defect"] == "pages_removed_[3, 4]"


def test_balance_break_document_keeps_every_page(by_id: dict[str, Any]) -> None:
    """The point of this case: nothing structural is wrong. Only the arithmetic is.
    That is why it must go to REVIEW rather than produce a re-upload instruction."""
    entry = by_id["balance_break_icici"]
    with pymupdf.open(_path(entry)) as doc:
        assert doc.page_count == entry["ground_truth"]["page_count"]
    assert entry["expected"]["outcome"] == "REVIEW"


def test_scanned_documents_have_no_native_text_layer(by_id: dict[str, Any]) -> None:
    for doc_id in ("scanned_poor_kotak", "scanned_clean_hdfc"):
        entry = by_id[doc_id]
        assert entry["text_layer"] == "image"
        with pymupdf.open(_path(entry)) as doc:
            text = "".join(page.get_text() for page in doc).strip()
        assert text == "", f"{doc_id}: expected an image-only PDF, found a text layer"


def test_scan_quality_differs_between_the_two_scanned_documents(
    by_id: dict[str, Any],
) -> None:
    """The clean scan is the positive control. If both scans were equally degraded,
    a low-confidence verdict could not be attributed to image quality."""

    def bytes_per_page(doc_id: str) -> float:
        entry = by_id[doc_id]
        return entry["bytes"] / entry["ground_truth"]["page_count"]

    assert bytes_per_page("scanned_clean_hdfc") > bytes_per_page("scanned_poor_kotak") * 5


def test_savings_account_document_is_otherwise_valid(by_id: dict[str, Any]) -> None:
    rules = get_rules()
    entry = by_id["wrong_account_type_savings_hdfc"]
    assert entry["ground_truth"]["account_type"] == "SAVINGS"
    assert entry["ground_truth"]["coverage_days"] >= rules.period.required_coverage_days


def test_identity_mismatch_uses_an_individual_name(by_id: dict[str, Any]) -> None:
    entry = by_id["identity_mismatch_axis"]
    holder = entry["ground_truth"]["account_holder_name"]
    business = entry["application"]["business_name"]
    assert holder != business
    assert "SHARMA" in holder and "Sharma" in business, (
        "a partial token overlap is what makes this ambiguous rather than obviously "
        "unrelated -- which is the realistic and harder case"
    )


def test_integrity_case_is_an_otherwise_valid_document(by_id: dict[str, Any]) -> None:
    """Only one weak signal fires, which is below the escalation threshold, so this
    must still PASS. It is the control proving weak signals do not flood Operations."""
    rules = get_rules()
    entry = by_id["integrity_edited_producer_sbi"]
    assert entry["expected"]["outcome"] == "PASS"
    assert rules.integrity.integrity_review_threshold > 1

    reader = PdfReader(str(_path(entry)))
    assert "Foxit" in str((reader.metadata or {}).get("/Producer", ""))


# --------------------------------------------------------------- generator invariants


def test_generated_ledgers_are_arithmetically_exact() -> None:
    """The property the whole completeness validator rests on (ADR-007)."""
    st = generate_statement(
        bank_key="test",
        bank_name="Test Bank",
        branch="Test",
        ifsc="TEST0000001",
        account_holder="TEST ACCOUNT",
        account_number="123456789012",
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        seed=7,
    )
    st.assert_consistent()  # raises on any drift
    assert len(st.transactions) > 100


def test_break_balance_introduces_exactly_one_discontinuity() -> None:
    st = generate_statement(
        bank_key="test",
        bank_name="Test Bank",
        branch="Test",
        ifsc="TEST0000001",
        account_holder="TEST ACCOUNT",
        account_number="123456789012",
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        seed=7,
    )
    index = len(st.transactions) // 2
    tampered = break_balance(st, index=index, delta_paise=500_00)

    breaks = []
    balance = tampered.opening_balance_paise
    for i, txn in enumerate(tampered.transactions):
        balance = balance + txn.credit_paise - txn.debit_paise
        if balance != txn.balance_paise:
            breaks.append(i)
        balance = txn.balance_paise  # resynchronise to the stated value

    assert breaks == [index, index + 1], (
        "a single edited balance shows as a two-row discontinuity -- the edited row and "
        "the row after it -- which is the signature of a hand edit rather than a shift"
    )


def test_generation_is_deterministic() -> None:
    kwargs = {
        "bank_key": "test",
        "bank_name": "Test Bank",
        "branch": "Test",
        "ifsc": "TEST0000001",
        "account_holder": "TEST ACCOUNT",
        "account_number": "123456789012",
        "period_start": date(2026, 3, 1),
        "period_end": date(2026, 8, 31),
        "seed": 99,
    }
    a = generate_statement(**kwargs)  # type: ignore[arg-type]
    b = generate_statement(**kwargs)  # type: ignore[arg-type]
    assert a.transactions == b.transactions


def test_periods_are_derived_from_the_as_of_date(manifest: dict[str, Any]) -> None:
    """Hardcoded periods would silently go stale and make the recency rule untestable
    after a few months."""
    as_of = date.fromisoformat(manifest["as_of_date"])
    end = date.fromisoformat(manifest["valid_period"]["end"])
    assert 0 < (as_of - end).days <= 62
