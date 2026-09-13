"""The validation-rule config is a product artefact, not an implementation detail.

These tests pin the values that `docs/VALIDATION_RULES.md` documents, so config and
documentation cannot drift apart silently, and assert the invariants that make the
outcome model sound.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.rules import ValidationRules, get_rules, load_rules
from app.config.settings import get_settings


@pytest.fixture(scope="module")
def rules() -> ValidationRules:
    return get_rules()


def test_rules_file_loads(rules: ValidationRules) -> None:
    assert rules.version == 1
    assert rules.document_type == "bank_statement"


def test_documented_thresholds_match_config(rules: ValidationRules) -> None:
    """Pinned against docs/VALIDATION_RULES.md section 9."""
    assert rules.period.required_coverage_days == 180  # VERIFIED
    assert rules.period.coverage_tolerance_days == 3
    assert rules.period.recency_tolerance_days == 35  # ASSUMPTION
    assert rules.period.reference_date_source == "application_created_at"
    assert rules.file.max_file_size_mb == 20
    assert rules.file.max_password_attempts == 5
    assert rules.completeness.balance_tolerance_paise == 100
    assert rules.integrity.integrity_review_threshold == 2
    assert rules.confidence.pass_threshold == 0.80
    assert rules.confidence.fix_threshold == 0.65
    assert rules.confidence.rule_evidence_threshold == 0.75


def test_pass_is_never_easier_than_fix(rules: ValidationRules) -> None:
    """ADR-009: asserting 'this document is fine' needs at least as much confidence
    as asserting 'this one specific thing is wrong'."""
    assert rules.confidence.pass_threshold >= rules.confidence.fix_threshold


def test_confidence_weights_are_coherent(rules: ValidationRules) -> None:
    c = rules.confidence
    assert c.field_score_weight + c.rule_coverage_weight == pytest.approx(1.0)
    assert sum(c.field_weights.values()) == pytest.approx(1.0)


def test_reference_date_is_not_wall_clock(rules: ValidationRules) -> None:
    """Anchoring recency to 'today' would make a verdict change over time for the
    same document -- breaking the determinism requirement (PRODUCT_SPEC section 11)."""
    assert rules.period.reference_date_source != "now"


def test_reason_priority_is_causally_ordered(rules: ValidationRules) -> None:
    """Each level makes the levels below it unmeasurable, so the order matters."""
    rank = rules.reason_rank
    assert rank("FILE_CORRUPT") < rank("DOC_TYPE_MISMATCH")
    assert rank("DOC_TYPE_MISMATCH") < rank("PERIOD_INSUFFICIENT_COVERAGE")
    assert rank("PERIOD_INSUFFICIENT_COVERAGE") < rank("COMPLETENESS_MISSING_PAGES")
    assert rank("COMPLETENESS_MISSING_PAGES") < rank("READABILITY_NO_TEXT")


def test_priority_list_has_no_duplicates(rules: ValidationRules) -> None:
    codes = rules.primary_reason_priority
    assert len(codes) == len(set(codes))


def test_unknown_reason_code_sorts_last(rules: ValidationRules) -> None:
    assert rules.reason_rank("NOT_A_REAL_CODE") == len(rules.primary_reason_priority)


def test_account_type_absence_does_not_fail_the_document(rules: ValidationRules) -> None:
    """Not every Indian statement prints the account type; treating absence as a
    failure would tell customers to resend a document they already sent."""
    assert rules.document.account_type_absent_behaviour == "not_evaluated"


def test_dates_are_parsed_day_first(rules: ValidationRules) -> None:
    """03/04/2026 is 3 April in India. Getting this wrong yields a confidently
    wrong period verdict."""
    assert rules.period.date_order == "day_first"


def test_malformed_config_is_rejected_at_load(tmp_path) -> None:
    """A bad config must fail loudly at startup rather than silently fall back to
    defaults and issue wrong verdicts."""
    good = get_settings().validation_rules_path.read_text(encoding="utf-8")
    broken = good.replace("pass_threshold: 0.80", "pass_threshold: 0.10")
    path = tmp_path / "broken.yaml"
    path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValidationError, match="pass_threshold"):
        load_rules(path)


def test_unknown_config_key_is_rejected(tmp_path) -> None:
    """Typos in config must not be silently ignored."""
    good = get_settings().validation_rules_path.read_text(encoding="utf-8")
    path = tmp_path / "typo.yaml"
    path.write_text(good + "\nunexpected_section:\n  foo: 1\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_rules(path)
