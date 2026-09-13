"""R-IDN-001 — does the account belong to the applicant?

The only cross-document check in the MVP. The PAN ↔ GST ↔ Bank engine is explicitly
post-MVP.

Both outcomes below REVIEW rather than FIX, and that is a product decision rather than a
technical limitation: an MSME's registered legal name routinely differs from its bank
account name, its trade name, and its proprietor's personal name, and a sole
proprietor's current account may legitimately be in an individual's name. Automatically
telling such a customer "this is the wrong account" would be wrong often enough to
damage trust in every other message the system sends. A human resolves it in seconds.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.config.rules import ValidationRules
from app.domain.validation import RuleResult, RuleStatus, ValidationContext

_PUNCTUATION = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def normalise_name(name: str, legal_suffixes: list[str]) -> str:
    """Strip case, punctuation and legal form so only the distinctive part remains.

    "Sharma Metal Works Private Limited" and "SHARMA METAL WORKS PVT LTD" are the same
    business written two ways; without this they score as a partial match and every
    legitimate application would land in the review queue.
    """
    text = _PUNCTUATION.sub(" ", name.lower())
    text = _WHITESPACE.sub(" ", text).strip()

    # Longest suffixes first: "private limited" must be removed before "limited".
    for suffix in sorted(legal_suffixes, key=len, reverse=True):
        cleaned = _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", suffix.lower())).strip()
        if cleaned and text.endswith(f" {cleaned}"):
            text = text[: -len(cleaned) - 1].strip()
    return text


def similarity(left: str, right: str) -> float:
    """Blend sequence similarity with token overlap.

    Sequence ratio alone is fooled by reordering; token overlap alone is fooled by
    abbreviation. Taking the stronger of the two is more forgiving in the direction that
    matters: a false mismatch sends a legitimate application to a human for no reason.
    """
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    sequence = SequenceMatcher(None, left, right).ratio()
    left_tokens, right_tokens = set(left.split()), set(right.split())
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    return max(sequence, overlap)


def check_identity(ctx: ValidationContext, rules: ValidationRules) -> RuleResult:
    holder = ctx.statement.account_holder_name
    if not holder or not ctx.business_name:
        return RuleResult(
            rule_id="R-IDN-001",
            status=RuleStatus.NOT_EVALUATED,
            blocking=False,
            evidence={"reason": "account holder or business name unavailable"},
        )

    suffixes = rules.identity.legal_suffixes
    left = normalise_name(holder, suffixes)
    right = normalise_name(ctx.business_name, suffixes)
    score = similarity(left, right)

    evidence = {
        "account_holder": holder,
        "application_business_name": ctx.business_name,
        "normalised_holder": left,
        "normalised_business": right,
        "score": round(score, 3),
        "strong_match_threshold": rules.identity.identity_strong_match,
        "clear_mismatch_threshold": rules.identity.identity_clear_mismatch,
    }

    if score >= rules.identity.identity_strong_match:
        return RuleResult(
            rule_id="R-IDN-001", status=RuleStatus.PASS, blocking=False, evidence=evidence
        )

    reason = (
        "IDENTITY_AMBIGUOUS"
        if score >= rules.identity.identity_clear_mismatch
        else "IDENTITY_MISMATCH"
    )
    return RuleResult(
        rule_id="R-IDN-001",
        status=RuleStatus.FAIL,
        reason_code=reason,
        # Non-blocking: it cannot fail a document on its own, only route it to a human.
        blocking=False,
        evidence=evidence,
        evidence_confidence=abs(score - rules.identity.identity_clear_mismatch),
    )
