"""Render a Statement to a native-text PDF using a bank profile.

Native text (not images) is the default because that is what a bank-issued e-statement
is. The scanned variants in `degrade.py` are produced by rasterising these PDFs, which
keeps the OCR path honest: the same ledger appears in both, so extraction differences are
attributable to image quality rather than to different source data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from corpus.generator.ledger import Statement, Transaction
from corpus.generator.money import rupees
from corpus.generator.profiles import BankProfile

PAGE_W, PAGE_H = A4
MARGIN = 26.0
BODY_FONT_SIZE = 7.0
HEADER_ROW_HEIGHT = 10.5
FOOTER_RESERVE = 26.0


def _header_rows(st: Statement, profile: BankProfile) -> list[tuple[str, str]]:
    period = profile.period_format.format(
        start=st.period_start.strftime(profile.date_format),
        end=st.period_end.strftime(profile.date_format),
    )
    rows: list[tuple[str, str]] = [
        ("Account Name", st.account_holder),
        (profile.account_label, st.account_number),
    ]
    if profile.show_account_type:
        rows.append((profile.account_type_label, st.account_type))
    rows.append((profile.period_label, period))
    rows.extend(profile.extra_header_rows)
    return rows


def _layout(st: Statement, profile: BankProfile) -> tuple[float, float, int, float]:
    """Return (column-band y, first-row y, rows per page, line height).

    Derived from the profile rather than hardcoded, because the header block grows
    with the number of printed fields. A fixed offset silently painted the column
    band over the last header row -- which removed the statement period from the
    document entirely, making a valid statement look like it had no period at all.
    """
    top = PAGE_H - MARGIN
    rows = _header_rows(st, profile)
    band_y = top - 56 - HEADER_ROW_HEIGHT * len(rows) - 4

    line_height = 15.0 if profile.long_narration else 9.4
    first_row_y = band_y - 16
    if profile.per_page_balance_band:
        first_row_y -= 11

    bottom = MARGIN + FOOTER_RESERVE
    if profile.per_page_balance_band:
        bottom += 12
    rows_per_page = max(1, int((first_row_y - bottom) // line_height))
    return band_y, first_row_y, rows_per_page, line_height


def _fit(text: str, font: str, size: float, width: float) -> str:
    """Truncate to fit, preserving the tail of reference-bearing narration."""
    if stringWidth(text, font, size) <= width:
        return text
    ellipsis = "…"
    while text and stringWidth(text + ellipsis, font, size) > width:
        text = text[:-1]
    return text + ellipsis


def _wrap(text: str, font: str, size: float, width: float, max_lines: int) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if stringWidth(candidate, font, size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines = [text]
    lines[-1] = _fit(lines[-1], font, size, width)
    return lines[:max_lines]


def _cell_value(txn: Transaction, key: str, profile: BankProfile) -> str:
    match key:
        case "date":
            return txn.txn_date.strftime(profile.date_format)
        case "value_date":
            return txn.value_date.strftime(profile.date_format)
        case "description":
            return txn.description
        case "reference":
            return txn.reference
        case "debit":
            return rupees(txn.debit_paise) if txn.debit_paise else ""
        case "credit":
            return rupees(txn.credit_paise) if txn.credit_paise else ""
        case "amount_drcr":
            marker = "Cr" if txn.is_credit else "Dr"
            return f"{rupees(txn.amount_paise)} {marker}"
        case "balance":
            return rupees(txn.balance_paise)
    raise ValueError(f"unknown column key: {key}")


def _scaled_columns(profile: BankProfile) -> list[tuple[str, str, float, str, float]]:
    """Return (label, key, width, align, x) with widths scaled to the page."""
    available = PAGE_W - 2 * MARGIN
    total = sum(c.width for c in profile.columns)
    scale = available / total
    out: list[tuple[str, str, float, str, float]] = []
    x = MARGIN
    for col in profile.columns:
        width = col.width * scale
        out.append((col.label, col.key, width, col.align, x))
        x += width
    return out


def _draw_text(
    c: canvas.Canvas, text: str, x: float, y: float, width: float, align: str
) -> None:
    if align == "right":
        c.drawRightString(x + width - 4, y, text)
    elif align == "center":
        c.drawCentredString(x + width / 2, y, text)
    else:
        c.drawString(x + 2, y, text)


def _draw_header(c: canvas.Canvas, st: Statement, profile: BankProfile) -> None:
    top = PAGE_H - MARGIN
    c.setFillColorRGB(*profile.accent)
    c.setFont(f"{profile.font}-Bold", 13)
    c.drawString(MARGIN, top - 12, st.bank_name)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.setFont(profile.font, 7)
    c.drawString(MARGIN, top - 23, f"{st.branch}  |  IFSC: {st.ifsc}")

    c.setFillColorRGB(0, 0, 0)
    c.setFont(f"{profile.font}-Bold", 9)
    c.drawString(MARGIN, top - 42, "Statement of Account")

    c.setFont(profile.font, 7.5)
    y = top - 56
    for label, value in _header_rows(st, profile):
        c.setFillColorRGB(0.42, 0.42, 0.42)
        c.drawString(MARGIN, y, label)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(MARGIN + 94, y, str(value))
        y -= HEADER_ROW_HEIGHT

    # Column header band, repeated on every page as real statements do.
    band_y, _, _, _ = _layout(st, profile)
    c.setFillColorRGB(0.93, 0.93, 0.94)
    c.rect(MARGIN, band_y - 2, PAGE_W - 2 * MARGIN, 13, stroke=0, fill=1)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont(f"{profile.font}-Bold", 6.6)
    for label, _key, width, align, x in _scaled_columns(profile):
        _draw_text(c, label, x, band_y + 2, width, align)


def _draw_footer(
    c: canvas.Canvas, profile: BankProfile, page_no: int, total_pages: int
) -> None:
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.setFont(profile.font, 6)
    if profile.header_note:
        c.drawString(MARGIN, MARGIN - 2, profile.header_note)
    if profile.show_page_of_total:
        c.drawRightString(
            PAGE_W - MARGIN, MARGIN - 2, f"Page {page_no} of {total_pages}"
        )


def _paginate(
    st: Statement, profile: BankProfile, size: int
) -> list[list[Transaction]]:
    return [
        st.transactions[i : i + size] for i in range(0, len(st.transactions), size)
    ] or [[]]


def render_statement(st: Statement, profile: BankProfile, out_path: Path) -> int:
    """Render and return the page count."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _, first_row_y, rows_per_page, line_height = _layout(st, profile)
    pages = _paginate(st, profile, rows_per_page)
    total = len(pages)

    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle("Statement of Account")
    c.setAuthor(st.bank_name)
    # A bank-issued statement carries a server-side producer string. The integrity
    # validator (R-INT-001) reads this, so the corpus must set it deliberately.
    c.setProducer(f"{st.bank_name} Statement Service")
    c.setCreator(f"{st.bank_name} NetBanking")
    c.setSubject(
        f"Account statement {st.period_start.isoformat()} to {st.period_end.isoformat()}"
    )

    columns = _scaled_columns(profile)

    for page_index, rows in enumerate(pages, start=1):
        _draw_header(c, st, profile)

        opening = (
            st.opening_balance_paise
            if page_index == 1
            else pages[page_index - 2][-1].balance_paise
        )

        y = first_row_y
        if profile.per_page_balance_band:
            c.setFillColorRGB(0.35, 0.35, 0.35)
            c.setFont(profile.font, 6.4)
            c.drawString(MARGIN + 2, y + 11, f"Opening Balance: {rupees(opening)}")

        c.setFillColorRGB(0, 0, 0)
        c.setFont(profile.font, BODY_FONT_SIZE)

        # Zebra striping is painted for the whole page first. Drawing each stripe
        # immediately before its own row would paint over the wrapped second line of
        # the row above, clipping multi-line narration.
        stripe_y = y
        for row_index in range(len(rows)):
            if row_index % 2 == 1:
                c.setFillColorRGB(0.972, 0.972, 0.978)
                c.rect(
                    MARGIN,
                    stripe_y - (line_height - BODY_FONT_SIZE),
                    PAGE_W - 2 * MARGIN,
                    line_height,
                    stroke=0,
                    fill=1,
                )
            stripe_y -= line_height

        c.setFillColorRGB(0, 0, 0)
        c.setFont(profile.font, BODY_FONT_SIZE)

        for txn in rows:
            for _label, key, width, align, x in columns:
                value = _cell_value(txn, key, profile)
                if key == "description" and profile.long_narration:
                    lines = _wrap(value, profile.font, BODY_FONT_SIZE, width - 6, 2)
                    for offset, line in enumerate(lines):
                        _draw_text(c, line, x, y - offset * 6.8, width, align)
                else:
                    _draw_text(
                        c,
                        _fit(value, profile.font, BODY_FONT_SIZE, width - 6),
                        x,
                        y,
                        width,
                        align,
                    )
            y -= line_height

        if profile.per_page_balance_band and rows:
            c.setFillColorRGB(0.35, 0.35, 0.35)
            c.setFont(profile.font, 6.4)
            c.drawString(
                MARGIN + 2, y - 2, f"Closing Balance: {rupees(rows[-1].balance_paise)}"
            )

        if profile.closing_summary and page_index == total:
            _draw_closing_summary(c, st, profile, y - 16)

        _draw_footer(c, profile, page_index, total)
        c.showPage()

    c.save()
    return total


def _draw_closing_summary(
    c: canvas.Canvas, st: Statement, profile: BankProfile, y: float
) -> None:
    c.setFillColorRGB(0.96, 0.96, 0.97)
    c.rect(MARGIN, y - 46, PAGE_W - 2 * MARGIN, 52, stroke=0, fill=1)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont(f"{profile.font}-Bold", 7.4)
    c.drawString(MARGIN + 6, y - 6, "Statement Summary")
    c.setFont(profile.font, 7)
    entries = [
        ("Opening Balance", rupees(st.opening_balance_paise)),
        ("Total Withdrawals", rupees(st.total_debits_paise)),
        ("Total Deposits", rupees(st.total_credits_paise)),
        ("Closing Balance", rupees(st.closing_balance_paise)),
    ]
    row_y = y - 18
    for label, value in entries:
        c.setFillColorRGB(0.42, 0.42, 0.42)
        c.drawString(MARGIN + 6, row_y, label)
        c.setFillColorRGB(0, 0, 0)
        c.drawRightString(MARGIN + 220, row_y, value)
        row_y -= 9.5


def render_gst_certificate(
    out_path: Path, *, legal_name: str, trade_name: str, gstin: str, issued: date
) -> int:
    """A plausible GST registration certificate.

    This is the corpus's wrong-document case. It must be a *credible* wrong document --
    a real financial document with an official-looking layout -- so that R-DOC-001 is
    tested against something that could genuinely be confused with a statement, rather
    than against a blank page.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle("Registration Certificate")
    c.setProducer("GSTN Portal")
    c.setCreator("GSTN Portal")

    top = PAGE_H - MARGIN
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(PAGE_W / 2, top - 24, "Government of India")
    c.drawCentredString(PAGE_W / 2, top - 40, "Form GST REG-06")
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(PAGE_W / 2, top - 64, "Registration Certificate")
    c.setFont("Helvetica", 8)
    c.drawCentredString(PAGE_W / 2, top - 78, "[See Rule 10(1)]")

    rows = [
        ("Registration Number (GSTIN)", gstin),
        ("Legal Name", legal_name),
        ("Trade Name", trade_name),
        ("Constitution of Business", "Private Limited Company"),
        (
            "Address of Principal Place of Business",
            "Unit 402, Trade Centre, Andheri East, Mumbai 400059",
        ),
        ("Date of Liability", issued.strftime("%d/%m/%Y")),
        (
            "Period of Validity",
            "From " + issued.strftime("%d/%m/%Y") + "  To  Not Applicable",
        ),
        ("Type of Registration", "Regular"),
        ("Particulars of Approving Authority", "Deputy Commissioner, Mumbai Division"),
        ("Date of Issue of Certificate", issued.strftime("%d/%m/%Y")),
    ]

    y = top - 110
    c.setFont("Helvetica", 8.5)
    for label, value in rows:
        c.setFillColorRGB(0.42, 0.42, 0.42)
        c.drawString(MARGIN + 6, y, label)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(MARGIN + 230, y, value)
        c.setStrokeColorRGB(0.88, 0.88, 0.88)
        c.line(MARGIN, y - 6, PAGE_W - MARGIN, y - 6)
        y -= 24

    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawString(
        MARGIN,
        y - 10,
        "This is a system generated certificate and does not require a signature.",
    )
    c.showPage()
    c.save()
    return 1
