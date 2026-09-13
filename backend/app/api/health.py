"""Health and readiness endpoints.

`/health` is a liveness probe: it must not touch dependencies.
`/ready` checks the database and the validation-rule config, because a process that
cannot load its rules must not be routed traffic -- it would produce verdicts from
defaults, which is worse than being down.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config.rules import get_rules
from app.config.settings import get_settings
from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - surfaced as a status, not raised
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        rules = get_rules()
        checks["validation_rules"] = f"ok (v{rules.version}, {rules.document_type})"
    except Exception as exc:  # noqa: BLE001
        checks["validation_rules"] = f"error: {exc}"

    ok = all(v.startswith("ok") for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # safe_summary() never includes secret values.
    return {
        "status": "ok" if ok else "degraded",
        "checks": checks,
        "config": settings.safe_summary(),
    }
