"""Loader for the reading profile and bank identification table."""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import BACKEND_ROOT

PROFILE_DIR = BACKEND_ROOT / "app" / "config" / "bank_profiles"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnSynonyms(_Strict):
    date: list[str]
    value_date: list[str]
    description: list[str]
    reference: list[str]
    debit: list[str]
    credit: list[str]
    amount_drcr: list[str]
    balance: list[str]

    def as_mapping(self) -> dict[str, list[str]]:
        return self.model_dump()


class HeaderLabels(_Strict):
    account_holder_name: list[str]
    account_number: list[str]
    account_type: list[str]
    period: list[str]
    ifsc: list[str]


class ReadingProfile(_Strict):
    key: str
    bank_name: str | None
    name_patterns: list[str]
    ifsc_prefixes: list[str]
    date_formats: list[str]
    columns: ColumnSynonyms
    header_labels: HeaderLabels
    period_separators: list[str]
    page_marker_patterns: list[str]

    @functools.cached_property
    def page_markers(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.IGNORECASE) for p in self.page_marker_patterns]


class BankIdentity(_Strict):
    key: str
    bank_name: str
    name_patterns: list[str]
    ifsc_prefixes: list[str]


class BankTable(_Strict):
    banks: list[BankIdentity] = Field(default_factory=list)


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.lru_cache
def get_reading_profile() -> ReadingProfile:
    return ReadingProfile.model_validate(_load(PROFILE_DIR / "generic.yaml"))


@functools.lru_cache
def get_bank_table() -> BankTable:
    return BankTable.model_validate(_load(PROFILE_DIR / "banks.yaml"))


#: How far down page one the issuer's own name is expected to appear. Generous enough
#: for a logo block, an address, and a "Statement of Account" title; short enough to stop
#: before the first transaction row on every layout in the corpus.
MASTHEAD_LINES = 25


def identify_bank(
    text: str, ifsc: str | None = None, *, masthead_lines: int = MASTHEAD_LINES
) -> BankIdentity | None:
    """Identify the bank from the printed name, with IFSC as a cross-check.

    IFSC is tried first: it is a structured code, whereas a printed name can be split
    across cells, hyphenated, or mangled by OCR ("HDFCBankLtd" is a real OCR reading from
    this corpus).

    The name search is confined to the *masthead* -- the top of the first page, above the
    transaction table. This is not a performance tweak. A bank statement is full of other
    banks' names: every NEFT, IMPS and UPI narration carries the counterparty's bank, so
    searching the whole page makes the busiest account the least identifiable. A real
    Canara Bank statement in testing was reported as "HDFC Bank" because the customer had
    paid someone with an HDFC account. Issuers print their own name at the top; nobody
    else's name appears there.
    """
    table = get_bank_table()

    if ifsc:
        prefix = ifsc.strip().upper()[:4]
        for bank in table.banks:
            if prefix in bank.ifsc_prefixes:
                return bank

    masthead = "\n".join(text.splitlines()[:masthead_lines])
    # Spaces removed so a name broken across cells still matches.
    haystack = re.sub(r"\s+", "", masthead).upper()
    best: BankIdentity | None = None
    best_len = 0
    for bank in table.banks:
        for pattern in bank.name_patterns:
            needle = re.sub(r"\s+", "", pattern).upper()
            # Longest match wins: "HDFC Bank" should beat a bare "HDFC".
            if needle in haystack and len(needle) > best_len:
                best, best_len = bank, len(needle)
    return best
