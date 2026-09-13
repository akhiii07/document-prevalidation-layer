"""Validation stage of the pipeline.

Assembles the context the rules are allowed to see, runs the engine, and records the
verdict. Nothing here decides anything -- the decision is entirely in
`validation_engine`, and this module's job is to give it honest inputs and persist what
came back.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config.rules import get_rules
from app.domain.enums import DocumentStatus, Outcome
from app.domain.extraction import ExtractedStatement
from app.domain.validation import RuleResult, ValidationContext
from app.models.application import Application, DocumentRequirement
from app.models.document import Document
from app.services.document_state import transition
from app.services.messages import compose_pass_summary
from app.services.outcomes import record_outcome

logger = logging.getLogger("docverify.validation")


def run_validation(
    session: Session,
    document: Document,
    statement: ExtractedStatement,
    integrity_signals: list[RuleResult] | None = None,
) -> None:
    from app.services.validation_engine import validate

    requirement = session.get(DocumentRequirement, document.requirement_id)
    if requirement is None:
        raise LookupError(f"requirement {document.requirement_id} not found")
    application = session.get(Application, requirement.application_id)
    if application is None:
        raise LookupError(f"application {requirement.application_id} not found")

    transition(session, document, DocumentStatus.VALIDATING)

    context = ValidationContext(
        statement=statement,
        business_name=application.business_name,
        # Anchored to the application, not the wall clock: a document that validated
        # correctly today must not start failing on re-validation weeks later.
        reference_date=application.created_at.date(),
        integrity_signals=list(integrity_signals or []),
    )

    verdict = validate(context, get_rules())
    outcome = Outcome(verdict.outcome)

    message = compose_pass_summary(statement.to_json()) if outcome is Outcome.PASS else None

    record_outcome(
        session,
        document,
        outcome=outcome,
        reason_code=verdict.primary_reason_code,
        secondary_reason_codes=verdict.secondary_reason_codes or None,
        rule_results=[r.to_json() for r in verdict.results],
        composite_confidence=round(verdict.composite_confidence, 4),
        field_score=round(verdict.field_score, 4),
        rule_coverage=round(verdict.rule_coverage, 4),
        message=message,
        evidence=verdict.primary_evidence(),
    )

    logger.info(
        "document %s -> %s (%s) confidence=%.3f field=%.3f coverage=%.3f",
        document.id,
        verdict.outcome,
        verdict.primary_reason_code or "-",
        verdict.composite_confidence,
        verdict.field_score,
        verdict.rule_coverage,
    )
