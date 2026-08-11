"""B-6 reinforcement: regression tests for the platform_variants.status CHECK.

Same guard rails as ``test_content_status_constraint.py`` but for the
sibling column ``platform_variants.status`` (which previously had no
constraint at all):

1. The canonical ``PlatformVariantStatus`` enum covers every value the
   application code writes to ``platform_variants.status``.
2. The Alembic migration's CHECK SQL is sourced from the same enum.
3. End-to-end: ``alembic upgrade head`` against a fresh SQLite DB makes
   every enum value INSERT-able and rejects unknowns with IntegrityError.
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

from app.models.platform_variant_status import (  # noqa: E402
    PLATFORM_VARIANT_STATUSES,
    PlatformVariantStatus,
    platform_variant_status_check_sql,
)


# Mirrors the literal `pv.status = "..."` writes across the codebase, kept
# in lockstep manually so any new write that adds a value MUST be paired
# with both an enum extension and a migration.
CODE_WRITES_TO_PLATFORM_VARIANT_STATUS = {
    "draft",  # initial schema default
    "scheduled",  # workers/content_processor.py (newly-generated variants)
    "publishing",  # workers/publish_task.py + services/_publish_common.py
    "published",  # every publisher's success path
    "failed",  # every publisher's error path
    "assisted",  # services/publishers/assisted_publisher.py
}


def test_enum_covers_every_code_write() -> None:
    missing = CODE_WRITES_TO_PLATFORM_VARIANT_STATUS - PLATFORM_VARIANT_STATUSES
    assert not missing, (
        "Application code writes platform_variants.status values that the "
        f"canonical enum does not contain. Missing: {sorted(missing)}"
    )


def test_check_sql_contains_every_enum_value() -> None:
    sql = platform_variant_status_check_sql()
    for value in PlatformVariantStatus.values():
        assert f"'{value}'" in sql, f"CHECK SQL missing value: {value!r}"


def test_check_sql_uses_status_column_by_default() -> None:
    assert platform_variant_status_check_sql().startswith("status IN (")


# ---------------------------------------------------------------------------
# End-to-end: real Alembic chain → real CHECK behavior
# ---------------------------------------------------------------------------


def _run_alembic_upgrade_to_head(sqlite_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(cfg, "head")


@pytest.fixture
def migrated_sqlite(tmp_path: Path):
    db_path = tmp_path / "reforge_pv_status.db"
    _run_alembic_upgrade_to_head(db_path)
    sync_url = f"sqlite:///{db_path}"
    engine = create_engine(sync_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed_user_and_content(conn) -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    content_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, plan, "
            "llm_migration_feature_enabled, is_active, is_verified, "
            "created_at, updated_at) "
            "VALUES (:id, :email, :pw, 'free', 0, 1, 0, :now, :now)"
        ),
        {"id": user_id, "email": f"{user_id}@example.com", "pw": "x", "now": now},
    )
    conn.execute(
        text(
            "INSERT INTO content_items (id, user_id, status, created_at, updated_at) "
            "VALUES (:id, :uid, 'draft', :now, :now)"
        ),
        {"id": content_id, "uid": user_id, "now": now},
    )
    return user_id, content_id


@pytest.mark.parametrize("status", sorted(PLATFORM_VARIANT_STATUSES))
def test_every_canonical_status_is_insertable(migrated_sqlite, status: str) -> None:
    with migrated_sqlite.begin() as conn:
        _, content_id = _seed_user_and_content(conn)
        conn.execute(
            text(
                "INSERT INTO platform_variants "
                "(id, content_item_id, platform, status, retry_count) "
                "VALUES (:id, :cid, 'instagram', :status, 0)"
            ),
            {"id": str(uuid.uuid4()), "cid": content_id, "status": status},
        )


def test_unknown_status_is_rejected(migrated_sqlite) -> None:
    with migrated_sqlite.begin() as conn:
        _, content_id = _seed_user_and_content(conn)

    with pytest.raises(IntegrityError):
        with migrated_sqlite.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform_variants "
                    "(id, content_item_id, platform, status, retry_count) "
                    "VALUES (:id, :cid, 'instagram', 'publised', 0)"
                ),
                {"id": str(uuid.uuid4()), "cid": content_id},
            )
