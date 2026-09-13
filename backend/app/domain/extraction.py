"""Canonical extraction types.

`RawExtraction` is what an extractor produces: positioned text cells, plus a per-cell
confidence. Both the native-text and OCR paths produce exactly this shape, which is what
lets a single parser serve both -- and what makes their outputs genuinely comparable
rather than two separate pipelines that happen to agree.

`ExtractedStatement` is the canonical schema every bank layout normalises into. It holds
only the lending-relevant subset (`RESEARCH_BANK_FORMATS.md` section 3.4): no address, no
phone, no counterparty analysis. This product validates a document; it does not analyse
anyone's finances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.enums import TextLayer


@dataclass(frozen=True)
class Cell:
    """One positioned piece of text."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    confidence: float = 1.0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Page:
    number: int  # 1-based
    width: float
    height: float
    cells: list[Cell] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)

    @property
    def mean_confidence(self) -> float:
        if not self.cells:
            return 0.0
        return sum(c.confidence for c in self.cells) / len(self.cells)

    def low_confidence_fraction(self, threshold: float) -> float:
        """Share of cells read below `threshold`.

        A better quality signal than the mean: a page can average acceptably while a
        quarter of its numbers are unreadable, and in a bank statement the numbers are
        the part that matters.
        """
        if not self.cells:
            return 1.0
        return sum(1 for c in self.cells if c.confidence < threshold) / len(self.cells)


@dataclass
class RawExtraction:
    provider: str
    text_layer: TextLayer
    pages: list[Page] = field(default_factory=list)
    #: Pages present in the document, which may exceed `len(pages)` if extraction
    #: stopped early. The difference is itself a signal.
    document_page_count: int = 0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    aborted_early: bool = False

    @property
    def page_coverage(self) -> float:
        if not self.document_page_count:
            return 0.0
        return len(self.pages) / self.document_page_count

    @property
    def mean_confidence(self) -> float:
        pages = [p for p in self.pages if p.cells]
        if not pages:
            return 0.0
        return sum(p.mean_confidence for p in pages) / len(pages)

    def to_json(self) -> dict[str, Any]:
        """Compact persisted form. Cell geometry is dropped: it is large, and once
        parsing has run nothing downstream needs it."""
        return {
            "provider": self.provider,
            "text_layer": self.text_layer.value,
            "document_page_count": self.document_page_count,
            "extracted_pages": len(self.pages),
            "mean_confidence": round(self.mean_confidence, 4),
            "aborted_early": self.aborted_early,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ExtractedTransaction:
    txn_date: date | None
    description: str
    debit_paise: int | None
    credit_paise: int | None
    balance_paise: int | None
    page: int
    value_date: date | None = None
    reference: str | None = None

    @property
    def is_complete(self) -> bool:
        """Usable for the completeness rules: a date, exactly one amount, a balance."""
        has_one_amount = bool(self.debit_paise) != bool(self.credit_paise)
        return self.txn_date is not None and has_one_amount and self.balance_paise is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "txn_date": self.txn_date.isoformat() if self.txn_date else None,
            "value_date": self.value_date.isoformat() if self.value_date else None,
            "description": self.description,
            "debit_paise": self.debit_paise,
            "credit_paise": self.credit_paise,
            "balance_paise": self.balance_paise,
            "reference": self.reference,
            "page": self.page,
        }


@dataclass
class ExtractedStatement:
    """The canonical schema. Only lending-relevant fields (research 3.4)."""

    bank_name: str | None = None
    bank_key: str | None = None
    account_holder_name: str | None = None
    #: Held in full only in memory, for continuity checks. Never serialised unmasked.
    account_number: str | None = None
    account_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    page_count: int = 0
    transactions: list[ExtractedTransaction] = field(default_factory=list)
    text_layer: TextLayer = TextLayer.NONE
    #: Per-field confidence in [0,1], feeding the weighted field score
    #: (`VALIDATION_RULES.md` section 8.1).
    field_confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    page_numbering: list[tuple[int, int]] = field(default_factory=list)
    #: Fields the deterministic parser could not find and an LLM supplied.
    #: Recorded for auditability: a reviewer must be able to see which values were
    #: read directly and which were assisted (ADR-001).
    llm_assisted_fields: list[str] = field(default_factory=list)

    # --- signals the validators need, captured at parse time so the statement is a
    # --- self-contained input and survives being persisted and re-read.
    #: A transaction-table header was located on at least one page. The strongest
    #: single indicator that this is a bank statement at all.
    table_detected: bool = False
    #: Share of the document's pages that were actually read.
    page_coverage: float = 0.0
    #: Share of read pages that yielded usable text.
    readable_page_ratio: float = 0.0
    #: Mean per-cell read confidence (1.0 for a native text layer).
    mean_read_confidence: float = 0.0
    #: Extraction stopped early on poor read quality.
    aborted_early: bool = False

    @property
    def masked_account_number(self) -> str | None:
        """Masked everywhere outside the document itself (`PRODUCT_SPEC.md` section 12)."""
        if not self.account_number:
            return None
        tail = self.account_number[-4:]
        return "X" * max(0, len(self.account_number) - 4) + tail

    @property
    def complete_transactions(self) -> list[ExtractedTransaction]:
        return [t for t in self.transactions if t.is_complete]

    def to_json(self) -> dict[str, Any]:
        """Persisted form. The full account number never appears here."""
        return {
            "bank_name": self.bank_name,
            "bank_key": self.bank_key,
            "account_holder_name": self.account_holder_name,
            "account_number_masked": self.masked_account_number,
            "account_type": self.account_type,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "page_count": self.page_count,
            "text_layer": self.text_layer.value,
            "table_detected": self.table_detected,
            "page_coverage": round(self.page_coverage, 4),
            "readable_page_ratio": round(self.readable_page_ratio, 4),
            "mean_read_confidence": round(self.mean_read_confidence, 4),
            "aborted_early": self.aborted_early,
            "transaction_count": len(self.transactions),
            "complete_transaction_count": len(self.complete_transactions),
            "page_numbering": self.page_numbering,
            "warnings": self.warnings,
            "llm_assisted_fields": self.llm_assisted_fields,
            "transactions": [t.to_json() for t in self.transactions],
        }
