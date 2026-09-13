"""Build the synthetic corpus and its manifest.

Run:
    python -m corpus.generator.build [--as-of YYYY-MM-DD] [--seed N] [--out DIR]

Everything is deterministic: the same `--as-of` and `--seed` produce the same corpus.
Periods are derived from `--as-of` rather than hardcoded, so the corpus never goes stale
and the recency rule (R-PER-002) stays meaningful however long after generation the demo
is run.

The manifest is the contract between this generator and the test suite: it records, for
every file, the defect that was injected and the outcome the pipeline is expected to
reach. Phase 7 asserts against it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from corpus.generator import degrade, render
from corpus.generator.ledger import Statement, generate_statement
from corpus.generator.profiles import PROFILES, BankProfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "corpus" / "generated"

# The business on the loan application. Identity checks compare the statement's
# account holder against this.
APPLICATION_BUSINESS_NAME = "Sharma Metal Works Private Limited"
STATEMENT_ACCOUNT_HOLDER = "SHARMA METAL WORKS PVT LTD"


# --------------------------------------------------------------------------- dates


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _last_day_of_previous_month(d: date) -> date:
    return _month_start(d) - timedelta(days=1)


def full_months_period(
    as_of: date, *, months: int, months_ago: int = 0
) -> tuple[date, date]:
    """A period of `months` complete calendar months ending `months_ago` months back.

    Banks issue statements on monthly cycles, so whole-month periods are what a
    customer can actually produce.
    """
    end = _last_day_of_previous_month(_add_months(as_of, -months_ago))
    start = _add_months(end, -(months - 1))
    return start, end


# --------------------------------------------------------------------------- specs


@dataclass
class Entry:
    doc_id: str
    filename: str
    scenario: str
    defect: str | None
    expected_outcome: str
    expected_reason: str | None
    notes: str
    statement: Statement | None = None
    profile: BankProfile | None = None
    page_count: int = 0
    password: str | None = None
    text_layer: str = "native"
    business_name: str = APPLICATION_BUSINESS_NAME

    def to_manifest(self, path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        record: dict[str, Any] = {
            "id": self.doc_id,
            "file": self.filename,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "scenario": self.scenario,
            "defect": self.defect,
            "text_layer": self.text_layer,
            "expected": {
                "outcome": self.expected_outcome,
                "primary_reason_code": self.expected_reason,
            },
            "application": {
                "external_reference": f"FLX-{abs(hash(self.doc_id)) % 90000 + 10000}",
                "business_name": self.business_name,
            },
            "password": self.password,
            "notes": self.notes,
        }

        if self.statement is not None:
            st = self.statement
            record["ground_truth"] = {
                "bank_name": st.bank_name,
                "account_holder_name": st.account_holder,
                # Full account numbers are never written outside the document itself.
                "account_number_masked": st.masked_account_number(),
                "account_number_last4": st.account_number[-4:],
                "account_type": st.account_type
                if self.profile and self.profile.show_account_type
                else None,
                "period_start": st.period_start.isoformat(),
                "period_end": st.period_end.isoformat(),
                "coverage_days": (st.period_end - st.period_start).days,
                "page_count": self.page_count,
                "transaction_count": len(st.transactions),
                "opening_balance_paise": st.opening_balance_paise,
                "closing_balance_paise": st.closing_balance_paise,
            }
        else:
            record["ground_truth"] = None

        return record


def _statement_for(
    profile: BankProfile,
    *,
    period: tuple[date, date],
    seed: int,
    account_holder: str = STATEMENT_ACCOUNT_HOLDER,
    account_type: str = "CURRENT",
) -> Statement:
    return generate_statement(
        bank_key=profile.key,
        # Profiles that advertise multi-line narration must actually produce it,
        # otherwise the hardest extraction case is never exercised.
        verbose_narration=profile.long_narration,
        bank_name=profile.bank_name,
        branch=profile.branch,
        ifsc=profile.ifsc,
        account_holder=account_holder,
        account_number=profile.account_number,
        account_type=account_type,
        period_start=period[0],
        period_end=period[1],
        seed=seed,
    )


# --------------------------------------------------------------------------- build


def build(as_of: date, seed: int, out_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    valid_period = full_months_period(as_of, months=6)
    short_period = full_months_period(as_of, months=4)
    stale_period = full_months_period(as_of, months=6, months_ago=6)

    entries: list[Entry] = []

    def emit(entry: Entry) -> Entry:
        entries.append(entry)
        return entry

    # -- Scenario 1: valid statements, one per bank layout --------------------
    for offset, (key, profile) in enumerate(PROFILES.items()):
        st = _statement_for(profile, period=valid_period, seed=seed + offset)
        path = out_dir / f"valid_{key}.pdf"
        pages = render.render_statement(st, profile, path)
        emit(
            Entry(
                doc_id=f"valid_{key}",
                filename=path.name,
                scenario="1_valid_statement",
                defect=None,
                expected_outcome="PASS",
                expected_reason=None,
                notes=(
                    f"{profile.bank_name} layout. Six complete calendar months, "
                    "arithmetically consistent balances."
                    + (
                        " No printed account type and no page-of-total footer, so "
                        "R-DOC-002 and R-CMP-001 must report NOT_EVALUATED without "
                        "failing the document."
                        if not profile.show_account_type
                        else ""
                    )
                ),
                statement=st,
                profile=profile,
                page_count=pages,
            )
        )

    hdfc = PROFILES["hdfc"]
    axis = PROFILES["axis"]
    icici = PROFILES["icici"]
    kotak = PROFILES["kotak"]
    sbi = PROFILES["sbi"]

    # -- Scenario 2: wrong period --------------------------------------------
    st = _statement_for(hdfc, period=short_period, seed=seed + 20)
    path = out_dir / "wrong_period_short_hdfc.pdf"
    pages = render.render_statement(st, hdfc, path)
    emit(
        Entry(
            doc_id="wrong_period_short_hdfc",
            filename=path.name,
            scenario="2_wrong_period",
            defect="coverage_4_months",
            expected_outcome="FIX",
            expected_reason="PERIOD_INSUFFICIENT_COVERAGE",
            notes=(
                "Four complete months instead of six -- short by roughly 60 days, far "
                "outside the 3-day boundary tolerance. Re-uploading valid_hdfc.pdf "
                "against the same requirement closes the loop."
            ),
            statement=st,
            profile=hdfc,
            page_count=pages,
        )
    )

    st = _statement_for(sbi, period=stale_period, seed=seed + 21)
    path = out_dir / "wrong_period_stale_sbi.pdf"
    pages = render.render_statement(st, sbi, path)
    emit(
        Entry(
            doc_id="wrong_period_stale_sbi",
            filename=path.name,
            scenario="2_wrong_period",
            defect="stale_end_date",
            expected_outcome="FIX",
            expected_reason="PERIOD_STALE",
            notes=(
                "Six full months of coverage, but the period ended roughly six months "
                "ago. Tests recency (R-PER-002) independently of coverage (R-PER-001) -- "
                "these fail for different reasons and need different customer messages."
            ),
            statement=st,
            profile=sbi,
            page_count=pages,
        )
    )

    # -- Scenario 3: password protected --------------------------------------
    st = _statement_for(icici, period=valid_period, seed=seed + 30)
    plain = out_dir / "_tmp_password_source.pdf"
    pages = render.render_statement(st, icici, plain)
    path = out_dir / "password_protected_icici.pdf"
    password = "SHAR1503"  # synthetic; mirrors a name+DDMM convention
    degrade.encrypt_pdf(plain, path, password)
    plain.unlink()
    emit(
        Entry(
            doc_id="password_protected_icici",
            filename=path.name,
            scenario="3_password_protected",
            defect="aes256_encrypted",
            expected_outcome="PASS",
            expected_reason="FILE_PASSWORD_REQUIRED",
            notes=(
                "AES-256 encrypted, valid underlying statement. Expected flow: detect "
                "encryption -> prompt -> decrypt in memory -> PASS. The reason code is "
                "the gate that fires on the way, not a failure outcome."
            ),
            statement=st,
            profile=icici,
            page_count=pages,
            password=password,
        )
    )

    # -- Scenario 4: wrong document ------------------------------------------
    path = out_dir / "wrong_document_gst_certificate.pdf"
    render.render_gst_certificate(
        path,
        legal_name=APPLICATION_BUSINESS_NAME,
        trade_name="Sharma Metal Works",
        gstin="27AAGCS4821K1ZP",
        issued=date(as_of.year - 3, 6, 14),
    )
    emit(
        Entry(
            doc_id="wrong_document_gst_certificate",
            filename=path.name,
            scenario="4_wrong_document",
            defect="gst_registration_certificate",
            expected_outcome="FIX",
            expected_reason="DOC_TYPE_MISMATCH",
            notes=(
                "A credible wrong document: a real lending document the customer was "
                "also asked for, not a blank page. Has no transaction table, so "
                "structural classification should reject it confidently."
            ),
        )
    )

    # -- Scenario 5: corrupt / unreadable ------------------------------------
    st = _statement_for(axis, period=valid_period, seed=seed + 40)
    source = out_dir / "_tmp_corrupt_source.pdf"
    render.render_statement(st, axis, source)
    path = out_dir / "corrupt_truncated_axis.pdf"
    degrade.corrupt_file(source, path, seed=seed + 41)
    source.unlink()
    emit(
        Entry(
            doc_id="corrupt_truncated_axis",
            filename=path.name,
            scenario="5_corrupt_unreadable",
            defect="garbled_bytes",
            expected_outcome="FIX",
            expected_reason="FILE_CORRUPT",
            notes=(
                "Garbled transfer: the %PDF- signature survives but the body is noise, "
                "so the file reaches the parse check (R-FILE-003) rather than the type "
                "check (R-FILE-001). The generator asserts at build time that both "
                "parsers reject it -- plain truncation was not reliable, because "
                "tolerant parsers recover many truncated files and disagree with each "
                "other about which. Distinct from unreadable content (R-RED-001)."
            ),
        )
    )

    path = out_dir / "invalid_not_a_pdf.pdf"
    path.write_text("Bank statement attached in the next message.\n", encoding="utf-8")
    emit(
        Entry(
            doc_id="invalid_not_a_pdf",
            filename=path.name,
            scenario="5_corrupt_unreadable",
            defect="text_file_with_pdf_extension",
            expected_outcome="FIX",
            expected_reason="FILE_UNSUPPORTED_TYPE",
            notes=(
                "A text file renamed to .pdf. Must be caught by magic bytes, not by "
                "the extension or the client-supplied MIME type (R-FILE-001)."
            ),
        )
    )

    path = out_dir / "invalid_empty.pdf"
    path.write_bytes(b"")
    emit(
        Entry(
            doc_id="invalid_empty",
            filename=path.name,
            scenario="5_corrupt_unreadable",
            defect="zero_bytes",
            expected_outcome="FIX",
            expected_reason="FILE_EMPTY",
            notes="Zero-byte upload -- a failed send, which happens routinely on mobile.",
        )
    )

    # -- Scenario 6: incomplete ----------------------------------------------
    st = _statement_for(axis, period=valid_period, seed=seed + 50)
    source = out_dir / "_tmp_incomplete_source.pdf"
    pages = render.render_statement(st, axis, source)
    dropped = {3, 4}
    path = out_dir / "incomplete_missing_pages_axis.pdf"
    degrade.remove_pages(source, path, dropped)
    source.unlink()
    emit(
        Entry(
            doc_id="incomplete_missing_pages_axis",
            filename=path.name,
            scenario="6_incomplete",
            defect=f"pages_removed_{sorted(dropped)}",
            expected_outcome="FIX",
            expected_reason="COMPLETENESS_MISSING_PAGES",
            notes=(
                "Pages 3 and 4 removed. Two independent signals agree -- the page-of-total "
                "footer shows a gap AND the balance fails to carry across the seam -- "
                "which is what permits a confident, actionable FIX (R-CMP-003)."
            ),
            statement=st,
            profile=axis,
            page_count=pages - len(dropped),
        )
    )

    st = _statement_for(icici, period=valid_period, seed=seed + 60)
    tampered = degrade.break_balance(
        st, index=len(st.transactions) // 2, delta_paise=1_47_500_00
    )
    path = out_dir / "balance_break_icici.pdf"
    pages = render.render_statement(tampered, icici, path)
    emit(
        Entry(
            doc_id="balance_break_icici",
            filename=path.name,
            scenario="6_incomplete",
            defect="single_balance_edited",
            expected_outcome="REVIEW",
            expected_reason="COMPLETENESS_BALANCE_BREAK",
            notes=(
                "Every page present, one stated balance altered. Arithmetic alone cannot "
                "distinguish an edit from missing content, so this must route to a human "
                "rather than accuse the customer or send them to re-upload (ADR-008)."
            ),
            statement=tampered,
            profile=icici,
            page_count=pages,
        )
    )

    # -- Scenario 7: low-confidence extraction -------------------------------
    st = _statement_for(kotak, period=valid_period, seed=seed + 70)
    source = out_dir / "_tmp_scan_source.pdf"
    pages = render.render_statement(st, kotak, source)
    path = out_dir / "scanned_poor_kotak.pdf"
    degrade.rasterise(source, path, dpi=150, degrade=True, seed=seed + 71)
    emit(
        Entry(
            doc_id="scanned_poor_kotak",
            filename=path.name,
            scenario="7_low_confidence",
            defect="degraded_scan",
            expected_outcome="REVIEW",
            expected_reason="EXTRACTION_LOW_CONFIDENCE",
            notes=(
                "Photograph-of-a-printout artefacts: rotation, blur, washed contrast, "
                "sensor noise, downscaled. Should depress extraction confidence below "
                "the PASS threshold rather than yield a confidently wrong reading."
            ),
            statement=st,
            profile=kotak,
            page_count=pages,
            text_layer="image",
        )
    )

    path = out_dir / "scanned_clean_hdfc.pdf"
    st_clean = _statement_for(hdfc, period=valid_period, seed=seed + 80)
    clean_source = out_dir / "_tmp_scan_clean_source.pdf"
    pages = render.render_statement(st_clean, hdfc, clean_source)
    degrade.rasterise(clean_source, path, dpi=300, degrade=False, seed=seed + 81)
    clean_source.unlink()
    source.unlink()
    emit(
        Entry(
            doc_id="scanned_clean_hdfc",
            filename=path.name,
            scenario="7_low_confidence",
            defect="image_only_high_quality",
            expected_outcome="PASS",
            expected_reason=None,
            notes=(
                "Image-only but clean at 300 dpi. The positive control for the OCR path: "
                "proves low confidence tracks image quality rather than merely the "
                "absence of a text layer."
            ),
            statement=st_clean,
            profile=hdfc,
            page_count=pages,
            text_layer="image",
        )
    )

    # -- Additional rule coverage --------------------------------------------
    st = _statement_for(
        hdfc, period=valid_period, seed=seed + 90, account_type="SAVINGS"
    )
    path = out_dir / "wrong_account_type_savings_hdfc.pdf"
    pages = render.render_statement(st, hdfc, path)
    emit(
        Entry(
            doc_id="wrong_account_type_savings_hdfc",
            filename=path.name,
            scenario="8_wrong_account_type",
            defect="savings_account",
            expected_outcome="FIX",
            expected_reason="ACCOUNT_TYPE_NOT_CURRENT",
            notes=(
                "A valid statement for the wrong account. The requirement is specifically "
                "a current account (VERIFIED from FlexiLoans' published requirements)."
            ),
            statement=st,
            profile=hdfc,
            page_count=pages,
        )
    )

    st = _statement_for(
        axis,
        period=valid_period,
        seed=seed + 100,
        account_holder="RAJESH KUMAR SHARMA",
    )
    path = out_dir / "identity_mismatch_axis.pdf"
    pages = render.render_statement(st, axis, path)
    emit(
        Entry(
            doc_id="identity_mismatch_axis",
            filename=path.name,
            scenario="9_identity",
            defect="account_holder_is_an_individual",
            expected_outcome="REVIEW",
            expected_reason="IDENTITY_MISMATCH",
            notes=(
                "Account is in a person's name while the application is a private limited "
                "company. Often legitimate for a proprietor, which is exactly why this "
                "goes to a human instead of a FIX."
            ),
            statement=st,
            profile=axis,
            page_count=pages,
            business_name=APPLICATION_BUSINESS_NAME,
        )
    )

    st = _statement_for(sbi, period=valid_period, seed=seed + 110)
    source = out_dir / "_tmp_producer_source.pdf"
    pages = render.render_statement(st, sbi, source)
    path = out_dir / "integrity_edited_producer_sbi.pdf"
    degrade.strip_producer_metadata(source, path, producer="Foxit PhantomPDF 12.1")
    source.unlink()
    emit(
        Entry(
            doc_id="integrity_edited_producer_sbi",
            filename=path.name,
            scenario="10_integrity_signal",
            defect="consumer_editor_producer_metadata",
            expected_outcome="PASS",
            expected_reason=None,
            notes=(
                "Otherwise-valid statement re-saved by a consumer PDF editor. Exactly ONE "
                "integrity signal fires, which is below the escalation threshold of 2 -- "
                "so this must still PASS. It is the negative control proving weak signals "
                "do not flood Operations with noise."
            ),
            statement=st,
            profile=sbi,
            page_count=pages,
        )
    )

    # -- Manifest -------------------------------------------------------------
    manifest = {
        "schema_version": 1,
        "as_of_date": as_of.isoformat(),
        "seed": seed,
        "application_business_name": APPLICATION_BUSINESS_NAME,
        "valid_period": {
            "start": valid_period[0].isoformat(),
            "end": valid_period[1].isoformat(),
            "coverage_days": (valid_period[1] - valid_period[0]).days,
        },
        "warning": (
            "Synthetic documents for development and demonstration only. "
            "No real customer financial data."
        ),
        "documents": [e.to_manifest(out_dir / e.filename) for e in entries],
    }

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the synthetic bank-statement corpus."
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="Reference date the periods are derived from (YYYY-MM-DD).",
    )
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    manifest = build(args.as_of, args.seed, args.out)

    print(f"as-of {manifest['as_of_date']}  seed {manifest['seed']}  -> {args.out}")
    print(
        f"valid period: {manifest['valid_period']['start']} to "
        f"{manifest['valid_period']['end']} "
        f"({manifest['valid_period']['coverage_days']} days)\n"
    )
    width = max(len(d["id"]) for d in manifest["documents"])
    for doc in manifest["documents"]:
        reason = doc["expected"]["primary_reason_code"] or "-"
        print(
            f"  {doc['id']:<{width}}  {doc['expected']['outcome']:<6}  "
            f"{reason:<30}  {doc['bytes'] / 1024:7.1f} KB"
        )
    print(f"\n{len(manifest['documents'])} documents")


if __name__ == "__main__":
    main()
