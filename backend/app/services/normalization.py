"""Turning positioned text into a canonical statement.

One parser serves both the native-text and OCR paths, because both produce the same
positioned-cell shape. That is deliberate: two parsers would drift, and the OCR path
would quietly become the less-tested one.

The approach is geometric rather than textual. Column *labels* are located in the header
band, boundaries are derived from their positions, and every cell below is assigned to a
column by where it sits. Regex over flattened page text cannot survive multi-line
narration -- which research calls the single biggest extraction hazard (section 3.3) --
because once rows stop mapping one-to-one onto transactions, line-based parsing silently
merges or drops them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from app.config.bank_profiles import ReadingProfile, get_reading_profile, identify_bank
from app.config.rules import ValidationRules, get_rules
from app.domain.enums import TextLayer
from app.domain.extraction import (
    Cell,
    ExtractedStatement,
    ExtractedTransaction,
    Page,
    RawExtraction,
)
from app.providers.llm import LLMProvider, get_llm

logger = logging.getLogger("docverify.normalization")

_AMOUNT_RE = re.compile(r"^[\s₹Rs.]*(-?[\d,]+(?:\.\d{1,2})?)\s*(dr|cr)?\.?$", re.IGNORECASE)
_ACCOUNT_NUMBER_RE = re.compile(r"\b\d{8,20}\b")
_IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
#: Date-shaped tokens, used to recover a period when the separator is unreliable.
_DATE_TOKEN_RE = re.compile(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}")


# --------------------------------------------------------------------- primitives


def parse_amount_paise(text: str) -> tuple[int | None, str | None]:
    """Parse an Indian-formatted amount. Returns (paise, 'dr'|'cr'|None).

    Integer paise throughout: a float rounding error would show up as a balance break
    and make the completeness validator report a defect the document does not have.
    """
    cleaned = text.strip().replace("₹", "").replace("INR", "")
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        return None, None

    number = match.group(1).replace(",", "")
    marker = (match.group(2) or "").lower() or None
    try:
        whole, _, frac = number.partition(".")
        paise = int(whole) * 100 + int((frac + "00")[:2]) * (-1 if whole.startswith("-") else 1)
    except ValueError:
        return None, None
    return paise, marker


def parse_date(text: str, profile: ReadingProfile) -> date | None:
    """Parse a date, day-first.

    Day-first is not a preference but a correctness requirement: 03/04/2026 is 3 April
    in India, and reading it as 4 March produces a period verdict that is confidently
    wrong -- the worst failure mode available to this product.
    """
    candidate = text.strip().rstrip(".,")
    if not candidate:
        return None
    for fmt in profile.date_formats:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _rows(page: Page, tolerance: float) -> list[list[Cell]]:
    """Group cells into visual rows by vertical proximity."""
    ordered = sorted(page.cells, key=lambda c: (c.top, c.x0))
    rows: list[list[Cell]] = []
    for cell in ordered:
        if rows and abs(cell.top - rows[-1][0].top) <= tolerance:
            rows[-1].append(cell)
        else:
            rows.append([cell])
    for row in rows:
        row.sort(key=lambda c: c.x0)
    return rows


def _row_text(row: list[Cell]) -> str:
    return " ".join(c.text for c in row)


# --------------------------------------------------------------------- table layout


#: A header label we do not recognise. It still marks a column: cells beneath it belong
#: to *it*, not to whichever neighbour happens to be closer.
UNKNOWN_COLUMN = "__unknown__"


class ColumnLayout:
    """Column boundaries derived from the positions of the header labels."""

    def __init__(self, matches: list[tuple[str, float, float]]) -> None:
        # (concept, x0, x1), left to right.
        self.matches = sorted(matches, key=lambda m: m[1])
        self.concepts = [m[0] for m in self.matches]

        self.boundaries: list[float] = []
        for left, right in zip(self.matches, self.matches[1:], strict=False):
            # Midway between the left label's right edge and the right label's left
            # edge. Right-aligned numeric values sit left of their own label, so a
            # boundary at the label's edge would misfile them.
            self.boundaries.append((left[2] + right[1]) / 2)

    def column_of(self, cell: Cell) -> str | None:
        if not self.matches:
            return None
        index = 0
        while index < len(self.boundaries) and cell.center_x > self.boundaries[index]:
            index += 1
        concept = self.concepts[index]
        return None if concept == UNKNOWN_COLUMN else concept

    def assign(self, row: list[Cell]) -> dict[str, list[Cell]]:
        grouped: dict[str, list[Cell]] = {}
        for cell in row:
            concept = self.column_of(cell)
            if concept:
                grouped.setdefault(concept, []).append(cell)
        return grouped


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _match_label_at(row: list[Cell], start: int, target: str, used: set[int]) -> int | None:
    """If `target` starts at cell `start`, return the index of its last cell.

    Labels arrive fragmented: pdfplumber emits "Closing" and "Balance" as two words,
    while OCR returns "ClosingBalance" as one box. Both must match the same synonym, so
    matching accumulates consecutive cells until the joined text equals the label.
    """
    joined = ""
    for end in range(start, min(start + 5, len(row))):
        if end in used:
            return None
        joined += _normalise(row[end].text)
        if joined == target:
            return end
        if not target.startswith(joined):
            return None
    return None


def _match_header_row(
    row: list[Cell], profile: ReadingProfile, minimum: int
) -> ColumnLayout | None:
    """Detect the transaction-table header and locate each column label."""
    synonyms = profile.columns.as_mapping()
    matches: list[tuple[str, float, float]] = []
    used: set[int] = set()

    # Longest labels first, so "Closing Balance" wins over "Balance" and
    # "Withdrawal Amt" over "Withdrawal".
    ordered = sorted(
        ((concept, label) for concept, labels in synonyms.items() for label in labels),
        key=lambda pair: -len(pair[1]),
    )

    for concept, label in ordered:
        if any(m[0] == concept for m in matches):
            continue
        target = _normalise(label)
        for start in range(len(row)):
            if start in used:
                continue
            end = _match_label_at(row, start, target, used)
            if end is not None:
                matches.append((concept, row[start].x0, row[end].x1))
                used.update(range(start, end + 1))
                break

    if len(matches) < minimum:
        return None

    # Every *unmatched* header cell becomes a column of its own.
    #
    # Without this, an unrecognised label leaves a hole, and the hole is filled by
    # whichever neighbour is geometrically nearest -- so its values land in a column
    # that means something else. A real Canara Bank statement carries a "Dr/Cr" column
    # we have no synonym for; its "Dr" markers were absorbed into the date column, the
    # date then read as "24/04/2025 Dr", failed to parse, and *every single row* of the
    # statement was discarded. An unknown column is far less damaging than a wrong one.
    for index, cell in enumerate(row):
        if index not in used and cell.text.strip():
            matches.append((UNKNOWN_COLUMN, cell.x0, cell.x1))
    # A transaction table without a date column is not a transaction table -- and the
    # date is what distinguishes a data row from a continuation line.
    if not any(m[0] == "date" for m in matches):
        return None
    return ColumnLayout(matches)


# --------------------------------------------------------------------- header block


def _find_labelled_value(rows: list[list[Cell]], labels: list[str]) -> tuple[str | None, float]:
    """Find `label: value` in the header block. Returns (value, confidence)."""
    for row in rows:
        joined = re.sub(r"\s+", " ", _row_text(row)).strip()
        for label in labels:
            pattern = re.compile(rf"^{re.escape(label)}\s*[:\-]?\s+(.+)$", re.IGNORECASE)
            match = pattern.match(joined)
            if match and match.group(1).strip():
                confidence = sum(c.confidence for c in row) / len(row)
                return match.group(1).strip(), confidence
    return None, 0.0


def _parse_period(value: str, profile: ReadingProfile) -> tuple[date | None, date | None]:
    """Read a statement period, tolerating however the two dates are joined."""
    for separator in profile.period_separators:
        if separator in value:
            left, _, right = value.partition(separator)
            start = parse_date(left.strip(), profile)
            end = parse_date(right.strip(), profile)
            if start and end:
                return start, end

    # Fall back to finding date-shaped tokens anywhere in the value. OCR routinely
    # loses the spaces around the separator -- this corpus produces
    # "01/03/26to31/08/26" from a perfectly legible scan -- and splitting on a bare
    # hyphen is unsafe because the dates contain hyphens themselves.
    found = [
        parsed
        for token in _DATE_TOKEN_RE.findall(value)
        if (parsed := parse_date(token.strip(), profile))
    ]
    if len(found) >= 2:
        return found[0], found[-1]
    return None, None


def _page_numbering(page: Page, profile: ReadingProfile) -> tuple[int, int] | None:
    text = re.sub(r"\s+", " ", page.text)
    for pattern in profile.page_markers:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except (ValueError, IndexError):
                continue
    return None


# --------------------------------------------------------------------- main entry


def parse_statement(raw: RawExtraction, llm: LLMProvider | None = None) -> ExtractedStatement:
    profile = get_reading_profile()
    rules = get_rules()

    statement = ExtractedStatement(
        page_count=raw.document_page_count,
        text_layer=raw.text_layer,
        warnings=list(raw.warnings),
    )

    if not raw.pages:
        statement.field_confidence = dict.fromkeys(rules.confidence.field_weights, 0.0)
        statement.warnings.append("no pages could be read")
        return statement

    all_rows: list[list[list[Cell]]] = []
    for page in raw.pages:
        tolerance = page.height * rules.extraction.row_tolerance_ratio
        all_rows.append(_rows(page, tolerance))

    _parse_header(statement, raw, all_rows, profile, rules.extraction.min_table_columns)
    _assist_header(statement, raw, all_rows, profile, llm or get_llm())
    _parse_transactions(statement, raw, all_rows, profile, rules.extraction.min_table_columns)
    _score_confidence(statement, raw, rules)
    return statement


#: Rows of page one treated as the issuer's letterhead. Wide enough for a logo block,
#: an address and an account summary; short enough to stop above the first transaction.
MASTHEAD_ROWS = 25



@dataclass
class TableProbe:
    """What the transaction parser can make of the pages read so far."""

    table_found: bool
    transactions: int
    complete: int


def probe_table(pages: list[Page], rules: ValidationRules | None = None) -> TableProbe:
    """Run the real transaction parser over a partial read.

    Deliberately the *same* parser rather than a cheap approximation, because the whole
    value of the probe is that its answer matches what the full run would conclude. An
    approximation that drifts would abort reads it should have finished.
    """
    rules = rules or get_rules()
    profile = get_reading_profile()
    raw = RawExtraction(provider="probe", text_layer=TextLayer.OCR, pages=pages)
    all_rows = [
        _rows(page, page.height * rules.extraction.row_tolerance_ratio) for page in pages
    ]
    statement = ExtractedStatement()
    _parse_transactions(statement, raw, all_rows, profile, rules.extraction.min_table_columns)
    return TableProbe(
        table_found=statement.table_detected,
        transactions=len(statement.transactions),
        complete=len(statement.complete_transactions),
    )

def _masthead(
    page_rows: list[list[Cell]], profile: ReadingProfile, min_columns: int
) -> str:
    """The issuer's letterhead: page one, above the transaction table.

    Bounded by the table header rather than by a fixed row count, because the bound
    is what makes it meaningful. A bank statement is full of *other* banks' names --
    every NEFT, IMPS and UPI narration carries the counterparty's -- so a search that
    reaches the transaction rows makes the busiest account the least identifiable.
    Above the table, only the issuer appears.
    """
    limit = min(len(page_rows), MASTHEAD_ROWS)
    for index, row in enumerate(page_rows[:limit]):
        if _match_header_row(row, profile, min_columns) is not None:
            limit = index
            break
    return "\n".join(_row_text(row) for row in page_rows[:limit])


def _parse_header(
    statement: ExtractedStatement,
    raw: RawExtraction,
    all_rows: list[list[list[Cell]]],
    profile: ReadingProfile,
    min_columns: int,
) -> None:
    labels = profile.header_labels
    # The header block lives on page 1, but re-reading later pages costs nothing and
    # covers layouts that repeat the account block on every page.
    rows = [row for page_rows in all_rows[:2] for row in page_rows]

    holder, holder_conf = _find_labelled_value(rows, labels.account_holder_name)
    statement.account_holder_name = holder
    statement.field_confidence["account_holder_name"] = holder_conf if holder else 0.0

    number, number_conf = _find_labelled_value(rows, labels.account_number)
    if number:
        digits = _ACCOUNT_NUMBER_RE.search(number.replace(" ", ""))
        statement.account_number = digits.group(0) if digits else None
    statement.field_confidence["account_number"] = number_conf if statement.account_number else 0.0

    account_type, type_conf = _find_labelled_value(rows, labels.account_type)
    statement.account_type = account_type
    # Absence is not a failure: not every Indian statement prints the account type
    # (research 3.1), so R-DOC-002 must cope with it rather than issue a false FIX.
    statement.field_confidence["account_type"] = type_conf if account_type else 0.0

    period_value, period_conf = _find_labelled_value(rows, labels.period)
    if period_value:
        start, end = _parse_period(period_value, profile)
        statement.period_start, statement.period_end = start, end
    statement.field_confidence["period_start"] = period_conf if statement.period_start else 0.0
    statement.field_confidence["period_end"] = period_conf if statement.period_end else 0.0

    # Rows, not `Page.text`: that property joins every cell on the page with spaces,
    # so line structure -- the only thing separating the letterhead from the
    # narrations below it -- is lost before the search begins.
    masthead = _masthead(all_rows[0], profile, min_columns)
    # The IFSC search is confined to the masthead for the same reason, and more urgently:
    # IFSC is tried *first* inside `identify_bank`, so a counterparty's code lifted from
    # an NEFT narration would not merely compete with the issuer's name -- it would
    # silently outrank it.
    ifsc_match = _IFSC_RE.search(re.sub(r"\s+", "", masthead.upper()))
    bank = identify_bank(masthead, ifsc_match.group(1) if ifsc_match else None)
    if bank:
        statement.bank_name = bank.bank_name
        statement.bank_key = bank.key

    numbering = [n for page in raw.pages if (n := _page_numbering(page, profile))]
    statement.page_numbering = numbering


#: An assisted read is worth less than a direct one: the value was inferred from
#: context rather than matched against a printed label. Capping it means an
#: LLM-supplied field can contribute to a verdict but never carry one on its own.
LLM_ASSIST_CONFIDENCE = 0.70


def _assist_header(
    statement: ExtractedStatement,
    raw: RawExtraction,
    all_rows: list[list[list[Cell]]],
    profile: ReadingProfile,
    llm: LLMProvider,
) -> None:
    """Ask the LLM only for header fields the deterministic parser could not find.

    This is the whole of the LLM's role in the pipeline. It sees the header block of
    page one, with long digit runs masked, and nothing else -- no ledger, no account
    number, no verdict (`RESEARCH_REGULATORY.md` section 5).
    """
    missing = [
        field_name
        for field_name, value in (
            ("account_holder_name", statement.account_holder_name),
            ("account_type", statement.account_type),
            ("period_start", statement.period_start),
            ("period_end", statement.period_end),
        )
        if value is None
    ]
    # The deterministic provider returns nothing by design, so skip the work.
    if not missing or not llm.available or llm.name == "deterministic":
        return

    header_rows = all_rows[0][:25] if all_rows else []
    header_text = "\n".join(_row_text(row) for row in header_rows)
    if not header_text.strip():
        return

    supplied = llm.extract_header_fields(header_text, missing)
    if not supplied:
        return

    for field_name, value in supplied.items():
        if field_name == "account_holder_name" and statement.account_holder_name is None:
            statement.account_holder_name = value
        elif field_name == "account_type" and statement.account_type is None:
            statement.account_type = value
        elif field_name in ("period_start", "period_end"):
            parsed = parse_date(value, profile) or _iso_date(value)
            if parsed is None:
                continue
            if field_name == "period_start" and statement.period_start is None:
                statement.period_start = parsed
            elif field_name == "period_end" and statement.period_end is None:
                statement.period_end = parsed
        else:
            continue

        statement.llm_assisted_fields.append(field_name)
        statement.field_confidence[field_name] = LLM_ASSIST_CONFIDENCE

    if statement.llm_assisted_fields:
        logger.info("LLM supplied header fields: %s", statement.llm_assisted_fields)


def _iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_transactions(
    statement: ExtractedStatement,
    raw: RawExtraction,
    all_rows: list[list[list[Cell]]],
    profile: ReadingProfile,
    min_columns: int,
) -> None:
    transactions: list[ExtractedTransaction] = []
    candidate_rows = 0
    table_found = False

    for page, page_rows in zip(raw.pages, all_rows, strict=True):
        layout: ColumnLayout | None = None
        for index, row in enumerate(page_rows):
            if layout is None:
                layout = _match_header_row(row, profile, min_columns)
                if layout is not None:
                    table_found = True
                    logger.debug("page %s columns: %s", page.number, layout.concepts)
                continue

            grouped = layout.assign(row)
            parsed, is_candidate = _row_to_transaction(grouped, profile, page.number)
            candidate_rows += int(is_candidate)

            if parsed is not None:
                transactions.append(parsed)
            elif transactions and _is_continuation(grouped):
                # Multi-line narration: this row belongs to the transaction above it.
                extra = " ".join(c.text for c in grouped.get("description", []))
                if extra:
                    transactions[-1].description = f"{transactions[-1].description} {extra}".strip()
            del index

    statement.transactions = transactions
    statement.table_detected = table_found
    if candidate_rows and not transactions:
        statement.warnings.append("transaction rows were found but none could be parsed")


def _is_continuation(grouped: dict[str, list[Cell]]) -> bool:
    """A row with narration but no date and no money is a wrapped description."""
    if not grouped.get("description"):
        return False
    return not any(grouped.get(k) for k in ("date", "debit", "credit", "amount_drcr", "balance"))


def _amount_from(grouped: dict[str, list[Cell]], concept: str) -> tuple[int | None, str | None]:
    """Read an amount from a column, tolerating neighbours that spilled into it.

    Long narration routinely overruns its column, so a numeric column can contain
    description words as well as the figure. Joining everything and parsing the result
    fails, and silently loses the amount -- which shows up later as a transaction that
    cannot be reconciled, i.e. a false balance break.

    So: work right-to-left (Indian statements right-align figures) and take the first
    thing that parses, trying one, two, then three cells together so that a value and a
    detached "Dr"/"Cr" marker are read as one.
    """
    cells = grouped.get(concept, [])
    for end in range(len(cells), 0, -1):
        for span in (1, 2, 3):
            start = end - span
            if start < 0:
                continue
            text = " ".join(c.text for c in cells[start:end])
            amount, marker = parse_amount_paise(text)
            if amount is not None:
                return amount, marker
    return None, None


def _date_from(cells: list[Cell], profile: ReadingProfile) -> date | None:
    """Read a date from the date column, tolerating a neighbour that spilled into it.

    The whole joined string is tried first -- "12 Apr 2026" arrives as three cells and
    only means anything together. If that fails, each cell is tried alone, because one
    stray token must not cost us the row: a date column that reads "24/04/2025 Dr"
    parses as nothing at all, and a row without a date is not a transaction, so the
    entire statement silently becomes empty.
    """
    if not cells:
        return None
    joined = parse_date(" ".join(c.text for c in cells), profile)
    if joined is not None:
        return joined
    for cell in cells:
        parsed = parse_date(cell.text, profile)
        if parsed is not None:
            return parsed
    return None


def _row_to_transaction(
    grouped: dict[str, list[Cell]], profile: ReadingProfile, page_number: int
) -> tuple[ExtractedTransaction | None, bool]:
    """Convert one assigned row. Returns (transaction, was_a_transaction_candidate)."""
    date_cells = grouped.get("date", [])
    if not date_cells:
        return None, False

    txn_date = _date_from(date_cells, profile)
    if txn_date is None:
        return None, False

    def joined(concept: str) -> str:
        return " ".join(c.text for c in grouped.get(concept, [])).strip()

    debit_paise: int | None = None
    credit_paise: int | None = None

    if grouped.get("amount_drcr"):
        amount, marker = _amount_from(grouped, "amount_drcr")
        if amount is not None:
            if marker == "cr":
                credit_paise = amount
            elif marker == "dr":
                debit_paise = amount
            else:
                # A single amount column with no marker is ambiguous. Guessing would
                # invert the sign of every transaction, so it is left unset and the
                # row simply fails the completeness test.
                pass
    else:
        debit_paise, _ = _amount_from(grouped, "debit")
        credit_paise, _ = _amount_from(grouped, "credit")

    balance_paise, _ = _amount_from(grouped, "balance")

    return (
        ExtractedTransaction(
            txn_date=txn_date,
            value_date=parse_date(joined("value_date"), profile),
            description=joined("description"),
            debit_paise=debit_paise or None,
            credit_paise=credit_paise or None,
            balance_paise=balance_paise,
            reference=joined("reference") or None,
            page=page_number,
        ),
        True,
    )


def _score_confidence(statement: ExtractedStatement, raw: RawExtraction, rules) -> None:  # noqa: ANN001
    """Per-field confidence, feeding the weighted field score (VALIDATION_RULES 8.1)."""
    statement.page_coverage = raw.page_coverage
    statement.mean_read_confidence = raw.mean_confidence
    statement.aborted_early = raw.aborted_early
    # Readability is a judgement about the pages we *looked at*, so the denominator is
    # the pages read, not the pages in the document. The share we got through is
    # `page_coverage`, which is a different fact and already lowers confidence on its
    # own. Conflating the two made "we stopped early" indistinguishable from "this is
    # illegible", and told a customer their perfectly clear statement was a poor scan.
    readable = sum(1 for page in raw.pages if len(page.text.strip()) >= 50)
    statement.readable_page_ratio = readable / len(raw.pages) if raw.pages else 0.0

    complete = statement.complete_transactions
    total = len(statement.transactions)

    if total:
        # How much of what we parsed is actually usable, scaled by how well it was read
        # and how much of the document we got through.
        completeness = len(complete) / total
        statement.field_confidence["transactions"] = round(
            completeness * raw.mean_confidence * raw.page_coverage, 4
        )
    else:
        statement.field_confidence["transactions"] = 0.0

    # OCR pages that were read poorly drag the header fields down too: the same image
    # produced both, so a header read at 0.6 is not worth 1.0 just because a label
    # happened to match.
    if raw.text_layer.value == "OCR":
        for key in ("account_holder_name", "account_number", "account_type"):
            statement.field_confidence[key] = round(
                statement.field_confidence.get(key, 0.0) * raw.mean_confidence, 4
            )

    for key in rules.confidence.field_weights:
        statement.field_confidence.setdefault(key, 0.0)

    # No warning is added for an early abort: the extractor already recorded *why* it
    # stopped, and those warnings are copied onto the statement at construction. A
    # second, vaguer one would only contradict the first.
