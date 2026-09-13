"""Customer-facing message composition.

Every message is built from the rule's own **evidence**, so what the customer is told and
what a reviewer sees describe the same finding. A template that cannot quote the actual
values would drift from the verdict the moment either changed.

The binding rule (`PRODUCT_SPEC.md` §9): **a specific reason plus a specific next
action**. Nothing here may say "there was a problem with your document" — that is true,
useless, and leaves the customer with nothing to do.

Deliberately absent from customer text: reason codes, confidence numbers, rule ids,
model names, stack traces, and unmasked account numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.domain.enums import Outcome

#: Shown for every REVIEW. Never implies suspicion: most reviews are our uncertainty,
#: not the customer's fault, and the ones that are not are a human's call to make.
REVIEW_MESSAGE = (
    "Thanks — I've received your bank statement.\n\n"
    "This one needs a quick manual check by our team. "
    "We'll get back to you shortly. No action needed from you right now."
)


def _fmt(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        return date.fromisoformat(value).strftime("%d %b %Y")
    except (ValueError, TypeError):
        return str(value)


def _period(evidence: dict[str, Any]) -> str:
    return f"{_fmt(evidence.get('period_start'))} – {_fmt(evidence.get('period_end'))}"


def _insufficient_coverage(evidence: dict[str, Any]) -> str:
    months = round(evidence.get("required_days", 180) / 30)
    return (
        "I found one issue.\n\n"
        "Statement period:\n"
        f"❌ {_period(evidence)}\n\n"
        "Required:\n"
        f"✓ Latest {months} months\n\n"
        f"Please upload a statement covering the latest {months} months."
    )


def _stale_period(evidence: dict[str, Any]) -> str:
    return (
        "I found one issue.\n\n"
        "This statement ends on "
        f"{_fmt(evidence.get('period_end'))}, which is too far in the past.\n\n"
        "Please download the most recent statement from your bank and send that instead."
    )


def _wrong_document(evidence: dict[str, Any]) -> str:
    return (
        "This doesn't look like a bank statement — I can't find any transactions in it.\n\n"
        "Please send your 6-month current-account bank statement as a PDF."
    )


def _wrong_account_type(evidence: dict[str, Any]) -> str:
    printed = evidence.get("account_type", "")
    return (
        f"This statement is for a {printed.lower()} account.\n\n"
        "Please send the statement for your business current account."
    )


def _missing_pages(evidence: dict[str, Any]) -> str:
    missing = evidence.get("pages_missing") or []
    total = evidence.get("pages_total")
    if missing and total:
        pages = ", ".join(str(p) for p in missing[:6])
        detail = f"Pages {pages} of {total} are missing from the file you sent."
    else:
        detail = "Part of the statement is missing — the running balance doesn't carry through."
    return f"{detail}\n\nPlease send the complete statement, with all pages included."


def _no_text(evidence: dict[str, Any]) -> str:
    return (
        "I couldn't read any text in that document.\n\n"
        "Please send the PDF your bank issued, rather than a photo or a screenshot."
    )


def _partial_capture(evidence: dict[str, Any]) -> str:
    missing = evidence.get("missing_columns") or []
    if missing and set(missing) != {"amount", "balance"}:
        what = f"the {missing[0]} column is cut off"
    else:
        what = "the amount and balance columns are cut off"
    return (
        f"I can see your transactions and their dates, but {what}.\n\n"
        "This usually happens with screenshots of a banking app. Please send the "
        "statement PDF your bank issues — it has every column, and I can check "
        "it in a few seconds."
    )


def _poor_scan(evidence: dict[str, Any]) -> str:
    return (
        "That copy is too unclear for me to read reliably.\n\n"
        "Please send the PDF your bank issued, rather than a scan or photo of a printout."
    )


#: reason code -> (what is wrong, what to do), the two halves kept together so neither
#: can be written without the other.
_TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    "FILE_EMPTY": lambda e: (
        "The file you sent is empty.\n\nPlease send the bank statement PDF again."
    ),
    "FILE_TOO_LARGE": lambda e: (
        f"That file is larger than we can accept "
        f"({e.get('size', 0) / (1024 * 1024):.1f} MB, limit "
        f"{e.get('limit', 0) // (1024 * 1024)} MB).\n\n"
        "Please send the statement your bank issued, rather than a scanned copy."
    ),
    "FILE_UNSUPPORTED_TYPE": lambda e: (
        "That does not look like a PDF.\n\nPlease send the bank statement as a PDF file."
    ),
    "FILE_CORRUPT": lambda e: (
        "I could not open that file — it looks damaged or incomplete.\n\n"
        "Please download the statement from your bank again and resend it."
    ),
    "FILE_PASSWORD_INCORRECT": lambda e: (
        "That password did not open the document.\n\nPlease check the password and try again."
    ),
    "PERIOD_INSUFFICIENT_COVERAGE": _insufficient_coverage,
    "PERIOD_STALE": _stale_period,
    "DOC_TYPE_MISMATCH": _wrong_document,
    "ACCOUNT_TYPE_NOT_CURRENT": _wrong_account_type,
    "COMPLETENESS_MISSING_PAGES": _missing_pages,
    "READABILITY_PARTIAL_CAPTURE": _partial_capture,
    "READABILITY_NO_TEXT": _no_text,
    "READABILITY_POOR_SCAN": _poor_scan,
}

PASSWORD_PROMPT = "This PDF is password protected.\n\nPlease enter the PDF password."


def compose_customer_message(
    outcome: Outcome,
    reason_code: str | None,
    evidence: dict[str, Any] | None = None,
) -> str | None:
    if outcome is Outcome.PASS:
        return None
    if outcome is Outcome.REVIEW:
        return REVIEW_MESSAGE

    template = _TEMPLATES.get(reason_code or "")
    if template is None:
        # A FIX with no template would produce exactly the vague message the spec
        # forbids, so it becomes a review instead of a bad instruction.
        return REVIEW_MESSAGE
    try:
        return template(evidence or {})
    except (KeyError, TypeError, ValueError):
        return REVIEW_MESSAGE


def compose_pass_summary(normalized: dict[str, Any]) -> str:
    """The PASS confirmation (`PRODUCT_SPEC.md` §9)."""
    lines = ["Bank statement verified.", ""]
    if normalized.get("bank_name"):
        lines.append(f"Bank: {normalized['bank_name']}")
    if normalized.get("period_start") and normalized.get("period_end"):
        start = date.fromisoformat(normalized["period_start"]).strftime("%b %Y")
        end = date.fromisoformat(normalized["period_end"]).strftime("%b %Y")
        lines.append(f"Period: {start} – {end}")
    if normalized.get("page_count"):
        lines.append(f"Pages: {normalized['page_count']}")

    lines += [
        "",
        "✓ Document type",
        "✓ Required period",
        "✓ Readability",
        "✓ Completeness",
        "",
        "No further action is required.",
    ]
    return "\n".join(lines)
