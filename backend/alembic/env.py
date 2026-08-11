from __future__ import annotations

import asyncio
import os
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load .env (DATABASE_URL, etc.)
_repo_root_env = Path(__file__).resolve().parents[2] / ".env"
_backend_env = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=_repo_root_env, override=False)
load_dotenv(dotenv_path=_backend_env, override=False)

# Import metadata and models so autogenerate can "see" them.
# B-7: explicitly include `connection` so PlatformConnection is part of
# Base.metadata; otherwise `alembic revision --autogenerate` would silently
# omit the table and the schema drift could re-occur in the future.
from app.core.database import Base  # noqa: E402
from app.models import auth_models as _auth_models  # noqa: F401,E402
from app.models import content_orm as _content_orm  # noqa: F401,E402
from app.models import activity_orm as _activity_orm  # noqa: F401,E402
from app.models import social_orm as _social_orm  # noqa: F401,E402
from app.models import connection as _connection  # noqa: F401,E402

target_metadata = Base.metadata


def get_database_url() -> str:
    """
    Prefer DATABASE_URL; fall back to the same SQLite default as `app.core.config.Settings`
    so `alembic upgrade head` works in local dev without exporting env vars.
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    return "sqlite+aiosqlite:///./reforge.db"


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _ensure_wide_version_table(connection: Connection) -> None:
    """
    Alembic hardcodes `alembic_version.version_num` as VARCHAR(32) with no
    supported override (see alembic/ddl/impl.py `version_table_impl`), and
    only creates the table if it doesn't already exist. This project's
    revision IDs are longer descriptive strings (e.g.
    "006_platform_connections_and_drift" = 35 chars) — SQLite accepts them
    silently (no length enforcement) but Postgres raises
    StringDataRightTruncationError on the first migration that pushes past
    32 chars. Pre-creating the table with a wider column is Alembic's own
    documented workaround for this exact situation.
    """
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(255) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
            ")"
        )
    )
    # Commit this standalone statement now so it doesn't linger as an
    # autobegun transaction that `context.begin_transaction()` below would
    # then have to contend with.
    connection.commit()


def do_run_migrations(connection: Connection) -> None:
    _ensure_wide_version_table(connection)
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Override sqlalchemy.url from env
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

