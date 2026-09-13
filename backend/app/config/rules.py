"""Typed loader for `validation_rules.yaml`.

Rule thresholds are configuration, not code (PRODUCT_SPEC.md section 11). Loading them
through a typed model means a malformed or nonsensical config fails at startup with a
clear message, rather than silently producing wrong verdicts at runtime.
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.settings import get_settings


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileRules(_Strict):
    allowed_mime_types: list[str]
    magic_prefixes: list[str]
    max_file_size_mb: int = Field(gt=0)
    max_password_attempts: int = Field(gt=0)

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


class DocumentRules(_Strict):
    doc_type_confidence_threshold: float = Field(ge=0, le=1)
    account_type_absent_behaviour: str
    current_account_tokens: list[str]


class PeriodRules(_Strict):
    required_coverage_days: int = Field(gt=0)
    coverage_tolerance_days: int = Field(ge=0)
    recency_tolerance_days: int = Field(ge=0)
    reference_date_source: str
    date_order: str


class CompletenessRules(_Strict):
    balance_tolerance_paise: int = Field(ge=0)
    min_transactions_for_balance_check: int = Field(gt=0)


class ReadabilityRules(_Strict):
    min_readable_page_ratio: float = Field(ge=0, le=1)
    min_ocr_word_confidence: float = Field(ge=0, le=1)


class ExtractionRules(_Strict):
    ocr_dpi: int = Field(gt=0)
    ocr_probe_pages: int = Field(gt=0)
    ocr_abort_low_fraction: float = Field(ge=0, le=1)
    ocr_structural_probe_pages: int = Field(gt=0)
    ocr_max_pages: int = Field(gt=0)
    row_tolerance_ratio: float = Field(gt=0, le=0.1)
    min_table_columns: int = Field(gt=0)


class IntegrityRules(_Strict):
    integrity_review_threshold: int = Field(gt=0)
    consumer_editor_patterns: list[str]


class IdentityRules(_Strict):
    identity_strong_match: float = Field(ge=0, le=1)
    identity_clear_mismatch: float = Field(ge=0, le=1)
    legal_suffixes: list[str]

    @model_validator(mode="after")
    def _ordered(self) -> IdentityRules:
        if self.identity_clear_mismatch >= self.identity_strong_match:
            raise ValueError("identity_clear_mismatch must be below identity_strong_match")
        return self


class ConfidenceRules(_Strict):
    field_score_weight: float = Field(ge=0, le=1)
    rule_coverage_weight: float = Field(ge=0, le=1)
    pass_threshold: float = Field(ge=0, le=1)
    fix_threshold: float = Field(ge=0, le=1)
    rule_evidence_threshold: float = Field(ge=0, le=1)
    field_weights: dict[str, float]

    @model_validator(mode="after")
    def _coherent(self) -> ConfidenceRules:
        if abs(self.field_score_weight + self.rule_coverage_weight - 1.0) > 1e-9:
            raise ValueError("field_score_weight + rule_coverage_weight must equal 1.0")
        total = sum(self.field_weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"field_weights must sum to 1.0 (got {total})")
        # ADR-009: PASS must never be easier to reach than FIX.
        if self.pass_threshold < self.fix_threshold:
            raise ValueError(
                "pass_threshold must be >= fix_threshold: asserting a document is fine "
                "requires at least as much confidence as asserting one specific fault"
            )
        return self


class ReasonCode(_Strict):
    """One entry in the reason-code catalogue.

    This makes the tables in `PRODUCT_SPEC.md` section 8 executable rather than
    descriptive, so the documented outcome and the shipped behaviour cannot drift.
    """

    code: str
    outcome: str
    actionable: bool
    composite_gated: bool

    @model_validator(mode="after")
    def _coherent(self) -> ReasonCode:
        if self.outcome not in {"FIX", "REVIEW"}:
            raise ValueError(f"{self.code}: outcome must be FIX or REVIEW")
        if self.outcome == "REVIEW" and self.actionable:
            raise ValueError(
                f"{self.code}: a REVIEW cannot be customer-actionable -- if the customer "
                "can act on it, it is a FIX"
            )
        return self


class ValidationRules(_Strict):
    version: int
    document_type: str
    file: FileRules
    document: DocumentRules
    period: PeriodRules
    completeness: CompletenessRules
    readability: ReadabilityRules
    extraction: ExtractionRules
    integrity: IntegrityRules
    identity: IdentityRules
    confidence: ConfidenceRules
    gate_reason_codes: list[str]
    reason_codes: list[ReasonCode]

    @property
    def primary_reason_priority(self) -> list[str]:
        """Causal priority order, derived from the catalogue so the two cannot drift."""
        return [entry.code for entry in self.reason_codes]

    @property
    def known_reason_codes(self) -> set[str]:
        """Every code the engine can emit, including non-failure gates."""
        return {entry.code for entry in self.reason_codes} | set(self.gate_reason_codes)

    def reason(self, reason_code: str) -> ReasonCode | None:
        return next((e for e in self.reason_codes if e.code == reason_code), None)

    def is_actionable(self, reason_code: str) -> bool:
        entry = self.reason(reason_code)
        return bool(entry and entry.actionable)

    def is_composite_gated(self, reason_code: str) -> bool:
        entry = self.reason(reason_code)
        return entry.composite_gated if entry else True

    def default_outcome(self, reason_code: str) -> str:
        entry = self.reason(reason_code)
        return entry.outcome if entry else "REVIEW"

    def reason_rank(self, reason_code: str) -> int:
        """Position in the priority order. Unknown codes sort last."""
        codes = self.primary_reason_priority
        try:
            return codes.index(reason_code)
        except ValueError:
            return len(codes)


def load_rules(path: Path) -> ValidationRules:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ValidationRules.model_validate(raw)


@functools.lru_cache
def get_rules() -> ValidationRules:
    return load_rules(get_settings().validation_rules_path)
