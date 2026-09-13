"""Bank layout profiles.

Derived from `docs/RESEARCH_BANK_FORMATS.md` section 4, which is tagged **UNVERIFIED** --
public sources do not publish per-bank column specifications. These profiles therefore
exist to make the corpus *structurally diverse*, not to claim fidelity to any real bank's
output. Their purpose is to stop the extractor from being accidentally tuned to a single
layout.

The variation axes that matter (research section 3.3) are all represented across the set:
date formats, column labels, separate debit/credit vs. single amount with a Dr/Cr marker,
multi-line narration, per-page balance blocks, and "Page X of Y" footers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    label: str
    key: str  # date | value_date | description | reference | debit | credit | amount_drcr | balance
    width: float  # points
    align: str = "left"  # left | right | center


@dataclass(frozen=True)
class BankProfile:
    key: str
    bank_name: str
    branch: str
    ifsc: str
    account_number: str
    account_label: str
    date_format: str
    columns: list[Column]
    # Is the account type printed at all? Not every statement prints it
    # (research 3.1) -- R-DOC-002 must cope with absence.
    show_account_type: bool = True
    account_type_label: str = "Account Type"
    # "Page X of Y" footer. Common but not universal, so R-CMP-001 must degrade.
    show_page_of_total: bool = True
    # Axis-style per-page opening/closing balance band.
    per_page_balance_band: bool = False
    # HDFC-style closing summary block on the final page.
    closing_summary: bool = False
    # Kotak-style long narration that genuinely wraps across lines. Research 3.3
    # calls multi-line narration the single biggest extraction hazard, so at least
    # one profile must actually produce it.
    long_narration: bool = False
    header_note: str = ""
    font: str = "Helvetica"
    accent: tuple[float, float, float] = (0.15, 0.25, 0.45)
    period_label: str = "Statement Period"
    period_format: str = "{start} to {end}"
    extra_header_rows: list[tuple[str, str]] = field(default_factory=list)


PROFILES: dict[str, BankProfile] = {
    # Separate debit/credit columns, month-name dates, a value-date column,
    # 11-digit account number, dense utilitarian layout.
    "sbi": BankProfile(
        key="sbi",
        bank_name="State Bank of India",
        branch="Andheri East, Mumbai",
        ifsc="SBIN0011507",
        account_number="38294617502",
        account_label="Account Number",
        date_format="%d %b %Y",
        columns=[
            Column("Txn Date", "date", 62),
            Column("Value Date", "value_date", 58),
            Column("Description", "description", 196),
            Column("Debit", "debit", 66, "right"),
            Column("Credit", "credit", 66, "right"),
            Column("Balance", "balance", 72, "right"),
        ],
        accent=(0.13, 0.31, 0.55),
        header_note="This is a computer generated statement and does not require a signature.",
        extra_header_rows=[("CIF No", "88213460071")],
    ),
    # "Narration" label, Withdrawal/Deposit columns, short year dates,
    # page-of-total footer, closing summary block.
    "hdfc": BankProfile(
        key="hdfc",
        bank_name="HDFC Bank Ltd",
        branch="Lower Parel, Mumbai",
        ifsc="HDFC0000247",
        account_number="50200047183926",
        account_label="Account No",
        date_format="%d/%m/%y",
        columns=[
            Column("Date", "date", 54),
            Column("Narration", "description", 206),
            Column("Chq/Ref No", "reference", 74),
            Column("Withdrawal", "debit", 68, "right"),
            Column("Deposit", "credit", 68, "right"),
            Column("Closing Balance", "balance", 78, "right"),
        ],
        closing_summary=True,
        accent=(0.42, 0.09, 0.14),
        header_note="Statement generated on request. HDFC Bank Ltd.",
    ),
    # "Transaction Remarks" label, single amount column carrying a Dr/Cr marker.
    # This is the layout that breaks naive two-column parsers.
    "icici": BankProfile(
        key="icici",
        bank_name="ICICI Bank",
        branch="Bandra Kurla Complex, Mumbai",
        ifsc="ICIC0000104",
        account_number="010405001729",
        account_label="Account Number",
        date_format="%d-%m-%Y",
        columns=[
            Column("Tran Date", "date", 66),
            Column("Transaction Remarks", "description", 236),
            Column("Amount (Dr/Cr)", "amount_drcr", 96, "right"),
            Column("Balance", "balance", 82, "right"),
        ],
        accent=(0.55, 0.22, 0.06),
        period_label="Period",
        period_format="{start} - {end}",
    ),
    # "Particulars" label, per-page opening/closing balance band.
    "axis": BankProfile(
        key="axis",
        bank_name="Axis Bank",
        branch="Worli, Mumbai",
        ifsc="UTIB0000006",
        account_number="918020041627384",
        account_label="Account No",
        date_format="%d-%m-%Y",
        columns=[
            Column("Tran Date", "date", 62),
            Column("Particulars", "description", 214),
            Column("Debit", "debit", 70, "right"),
            Column("Credit", "credit", 70, "right"),
            Column("Balance", "balance", 76, "right"),
        ],
        per_page_balance_band=True,
        accent=(0.5, 0.06, 0.24),
    ),
    # Compact, long multi-line UPI/UTR narration, and -- deliberately -- no
    # printed account type and no page-of-total footer, so the corpus contains a
    # statement where R-DOC-002 and R-CMP-001 must report NOT_EVALUATED.
    "kotak": BankProfile(
        key="kotak",
        bank_name="Kotak Mahindra Bank",
        branch="Nariman Point, Mumbai",
        ifsc="KKBK0000958",
        account_number="4512096387",
        account_label="Account Number",
        date_format="%d-%m-%Y",
        columns=[
            Column("Date", "date", 58),
            Column("Narration", "description", 250),
            Column("Withdrawal (Dr)/Deposit (Cr)", "amount_drcr", 108, "right"),
            Column("Balance", "balance", 76, "right"),
        ],
        show_account_type=False,
        show_page_of_total=False,
        long_narration=True,
        accent=(0.62, 0.06, 0.12),
    ),
}
