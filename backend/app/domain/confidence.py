"""Composite confidence (ADR-009, `VALIDATION_RULES.md` §8).

Two components, deliberately:

* **field score** — per-field extraction confidence, weighted by how much each field
  matters to the rules that depend on it;
* **rule coverage** — how many blocking rules could actually be evaluated.

Rule coverage is what makes the number honest. A document where half the rules could not
run is not high-confidence however crisp the reading was, and a score built from field
confidence alone would say otherwise. "Confidence: 97%" with no provenance is decoration;
this returns its components so Operations can see *which* signal was weak rather than
just how weak the total was.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.rules import ConfidenceRules
from app.domain.validation import RuleResult, RuleStatus


@dataclass(frozen=True)
class ConfidenceBreakdown:
    composite: float
    field_score: float
    rule_coverage: float
    weakest_fields: list[tuple[str, float]]
    rules_evaluated: int
    rules_total: int

    def to_json(self) -> dict:
        return {
            "composite": round(self.composite, 4),
            "field_score": round(self.field_score, 4),
            "rule_coverage": round(self.rule_coverage, 4),
            "weakest_fields": [[name, round(value, 3)] for name, value in self.weakest_fields],
            "rules_evaluated": self.rules_evaluated,
            "rules_total": self.rules_total,
        }


def compose(
    field_confidence: dict[str, float],
    results: list[RuleResult],
    config: ConfidenceRules,
) -> ConfidenceBreakdown:
    field_score = sum(
        weight * float(field_confidence.get(name, 0.0))
        for name, weight in config.field_weights.items()
    )

    blocking = [r for r in results if r.blocking]
    evaluated = [r for r in blocking if r.status is not RuleStatus.NOT_EVALUATED]
    coverage = len(evaluated) / len(blocking) if blocking else 0.0

    composite = config.field_score_weight * field_score + config.rule_coverage_weight * coverage

    weakest = sorted(
        ((name, float(field_confidence.get(name, 0.0))) for name in config.field_weights),
        key=lambda pair: pair[1],
    )

    return ConfidenceBreakdown(
        composite=composite,
        field_score=field_score,
        rule_coverage=coverage,
        weakest_fields=[pair for pair in weakest if pair[1] < 1.0][:3],
        rules_evaluated=len(evaluated),
        rules_total=len(blocking),
    )
