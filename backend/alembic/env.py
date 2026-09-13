"""Alembic environment.

The database URL comes from application settings (environment), never from
alembic.ini, so credentials are not stored in a tracked file.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine

# Importing the models package registers every model on Base.metadata.
# Models arrive in Phase 4; the import is harmless until then.
import app.models  # noqa: F401,E402
from alembic import context
from app.config.settings import get_settings
from app.db.base import Base
from app.db.session import get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """Prefer an explicitly supplied URL, else application settings.

    `alembic.ini` carries no URL, so credentials stay out of tracked files. But callers
    that need to target a specific database -- CI, or the migration drift test -- must
    be able to say so; without this override they would silently migrate whatever the
    environment happened to point at.
    """
    override = config.get_main_option("sqlalchemy.url", None)
    return override or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most columns in place; batch mode rewrites the table.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    override = config.get_main_option("sqlalchemy.url", None)
    connectable = create_engine(override) if override else get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
