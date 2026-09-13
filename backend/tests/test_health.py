from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_is_liveness_only(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_checks_database_and_rules(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["validation_rules"].startswith("ok")


def test_ready_never_leaks_secrets(client: TestClient) -> None:
    """Config is surfaced for operability; secret values must never appear."""
    raw = client.get("/ready").text
    for secret in ("dev-ops-secret-change-me", "dev-signing-key-change-me"):
        assert secret not in raw
    assert "api_key" not in raw.lower()


def test_llm_inactive_without_credentials(client: TestClient) -> None:
    """The system must be fully functional with the LLM disabled (ADR-010)."""
    assert client.get("/ready").json()["config"]["llm_active"] is False
