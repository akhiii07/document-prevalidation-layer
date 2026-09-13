"""Application settings.

All configuration arrives through the environment. Nothing secret is hardcoded and
nothing secret is rendered in logs or API responses -- see `safe_summary()`.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "MSME Document Pre-Validation Layer"
    app_env: str = Field(default="local", description="local | test | production")
    debug: bool = False

    # --- Database ----------------------------------------------------------
    # SQLite by default so the stack runs with zero external dependencies.
    # Set DATABASE_URL to a postgresql+psycopg:// URL for Postgres parity. See ADR-012.
    database_url: str = Field(default=f"sqlite:///{BACKEND_ROOT / 'var' / 'docverify.db'}")

    # --- Object storage ----------------------------------------------------
    # Local filesystem implementation of StorageProvider. Never web-served:
    # documents are only reachable through short-lived signed URLs (ADR-011).
    storage_dir: Path = BACKEND_ROOT / "var" / "documents"
    signed_url_ttl_seconds: int = 300

    # --- Security ----------------------------------------------------------
    # Gates Operations endpoints. Prototype-grade by design (ADR-011).
    ops_shared_secret: SecretStr = SecretStr("dev-ops-secret-change-me")
    # Signs download URLs and any other short-lived token.
    url_signing_key: SecretStr = SecretStr("dev-signing-key-change-me")

    # --- LLM ---------------------------------------------------------------
    # Absent key => deterministic fallback provider. The system must work fully
    # without this (ADR-010).
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5"
    llm_enabled: bool = True

    # --- Frontend ----------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Demo --------------------------------------------------------------
    # Serves the synthetic corpus to the demo UI so a walkthrough does not require
    # navigating a file picker. Off outside local: it reads from a directory, and
    # nothing that reads from a directory belongs in a deployed service without a
    # reason. The corpus is synthetic by construction (`corpus/README.md`).
    demo_mode: bool = True
    corpus_dir: Path = PROJECT_ROOT / "corpus" / "generated"

    # --- Worker ------------------------------------------------------------
    # Disabled in tests, which drive the worker a job at a time rather than racing a
    # background thread.
    run_worker: bool = True
    worker_poll_seconds: float = 1.0

    # --- Validation rules --------------------------------------------------
    validation_rules_path: Path = BACKEND_ROOT / "app" / "config" / "validation_rules.yaml"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Allow CORS_ORIGINS to be given as a comma-separated string."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def llm_active(self) -> bool:
        """True only when a real LLM call is possible AND permitted."""
        return self.llm_enabled and self.anthropic_api_key is not None

    def safe_summary(self) -> dict[str, object]:
        """Loggable configuration snapshot. Never includes secret values."""
        return {
            "app_env": self.app_env,
            "database_backend": "sqlite" if self.is_sqlite else "postgresql",
            "storage_dir": str(self.storage_dir),
            "llm_active": self.llm_active,
            "llm_model": self.llm_model if self.llm_active else None,
            "signed_url_ttl_seconds": self.signed_url_ttl_seconds,
            "run_worker": self.run_worker,
            "demo_mode": self.demo_mode,
        }


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
