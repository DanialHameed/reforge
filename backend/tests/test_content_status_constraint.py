"""Regression tests for the B-6 content_items.status CHECK mismatch.

Three guard rails:

1. The canonical ``ContentItemStatus`` enum covers every value the
   application code writes to ``content_items.status``.
2. The Alembic migration's CHECK SQL is sourced from the same enum, so it
   cannot drift away from the application.
3. End-to-end: programmatically run all migrations on a fresh SQLite DB and
   verify that every enum value is INSERT-able and that an unknown value is
   rejected with an ``IntegrityError``.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO_ROOT))

from app.models.content_status import (  # noqa: E402
    CONTENT_ITEM_STATUSES,
    ContentItemStatus,
    content_item_status_check_sql,
)


# ---------------------------------------------------------------------------
# Statuses the application code writes today, mirrored from a code grep over
# `*.status = "..."` and `status="..."` references that target ContentItem.
# Updating this list is intentional: any new value MUST be paired with a
# migration that extends the CHECK constraint, or this test will fail.
# ---------------------------------------------------------------------------
CODE_WRITES_TO_CONTENT_ITEM_STATUS = {
    "draft",  # api/v1/content.py:199, 229
    "processing",  # workers/content_processor.py:60, 298 + api/v1/content.py:431
    "scheduled",  # workers/content_processor.py:180
    "failed",  # workers/content_processor.py:33 + api/v1/content.py:443
    "completed",  # workers/content_processor.py:411
    "completed_fallback",  # workers/content_processor.py:411
    "error_fallback",  # workers/content_processor.py:258
}


def test_enum_covers_every_code_write() -> None:
    """Every literal status the app writes must be a member of the canonical enum."""
    missing = CODE_WRITES_TO_CONTENT_ITEM_STATUS - CONTENT_ITEM_STATUSES
    assert not missing, (
        "Application code writes content_items.status values that the canonical "
        f"enum does not contain. Missing: {sorted(missing)}"
    )


def test_check_sql_contains_every_enum_value() -> None:
    """The CHECK SQL the migration emits must list every enum value verbatim."""
    sql = content_item_status_check_sql()
    for value in ContentItemStatus.values():
        assert f"'{value}'" in sql, f"CHECK SQL is missing value: {value!r}"


def test_check_sql_uses_status_column_by_default() -> None:
    sql = content_item_status_check_sql()
    assert sql.startswith("status IN (")


def test_check_sql_can_target_arbitrary_column() -> None:
    sql = content_item_status_check_sql(column="content_items.status")
    assert sql.startswith("content_items.status IN (")


# ---------------------------------------------------------------------------
# End-to-end: run the full Alembic migration chain against a temp SQLite file
# and verify the CHECK behavior on real SQL inserts.
# ---------------------------------------------------------------------------


def _run_alembic_upgrade_to_head(sqlite_path: Path) -> None:
    """Apply ``alembic upgrade head`` against the given sqlite file.

    Important: this fixture deliberately constructs the Alembic ``Config``
    *without* pointing at ``alembic.ini``. ``alembic/env.py`` calls
    ``fileConfig(config.config_file_name)`` only when an INI file is provided,
    and ``fileConfig`` defaults to ``disable_existing_loggers=True`` — which
    would silence every non-Alembic logger in the process for the rest of the
    test session. We supply ``script_location`` programmatically and let the
    application's existing logging configuration stand untouched.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(cfg, "head")


@pytest.fixture
def migrated_sqlite(tmp_path: Path):
    """Provide a fresh SQLite DB with the full Alembic chain applied."""
    db_path = tmp_path / "reforge_test.db"
    _run_alembic_upgrade_to_head(db_path)
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_user(conn) -> str:
    user_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, plan, "
            "llm_migration_feature_enabled, is_active, is_verified, "
            "created_at, updated_at) "
            "VALUES (:id, :email, :pw, 'free', 0, 1, 0, :now, :now)"
        ),
        {
            "id": user_id,
            "email": f"{user_id}@example.com",
            "pw": "x",
            "now": datetime.now(timezone.utc),
        },
    )
    return user_id


@pytest.mark.parametrize("status", sorted(CONTENT_ITEM_STATUSES))
def test_every_canonical_status_is_insertable(
    migrated_sqlite, status: str
) -> None:
    """Each value in the enum must satisfy the live DB CHECK constraint."""
    with migrated_sqlite.begin() as conn:
        user_id = _insert_user(conn)
        conn.execute(
            text(
                "INSERT INTO content_items (id, user_id, status, created_at, updated_at) "
                "VALUES (:id, :uid, :status, :now, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "status": status,
                "now": datetime.now(timezone.utc),
            },
        )


def test_unknown_status_is_rejected_by_check_constraint(migrated_sqlite) -> None:
    """The CHECK constraint must still reject anything outside the enum."""
    with migrated_sqlite.begin() as conn:
        user_id = _insert_user(conn)

    with pytest.raises(IntegrityError):
        with migrated_sqlite.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO content_items (id, user_id, status, created_at, updated_at) "
                    "VALUES (:id, :uid, 'totally-not-a-real-status', :now, :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "uid": user_id,
                    "now": datetime.now(timezone.utc),
                },
            )
