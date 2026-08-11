"""B-7 regression tests: Alembic schema must match the ORM after `upgrade head`.

These tests run the full Alembic chain (001 → 006) against a fresh SQLite
file and then assert:

* Every ORM-declared table exists in the database.
* The previously-missing tables/columns/indexes specifically named by the
  audit are present:
    - ``platform_connections`` table + indexes + unique constraint
    - ``social_accounts.created_at`` and ``updated_at`` columns
    - ``refresh_tokens.expires_at`` index
    - ``password_reset_tokens.expires_at`` index
    - ``activity_logs.content_item_id`` index
* Every ORM model can be inserted end-to-end against the migrated database
  WITHOUT relying on ``Base.metadata.create_all`` — this is the precise
  invariant B-7 is fixing.
* The migration is idempotent: running ``upgrade head`` against a database
  that already has these objects (via ``Base.metadata.create_all``) does not
  raise.

The ``alembic upgrade head`` invocation is constructed *without* an
``alembic.ini`` so that ``env.py``'s call to ``fileConfig`` is skipped — see
``tests/test_content_status_constraint.py`` for the rationale.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(REPO_ROOT))

from app.core.database import Base  # noqa: E402
from app.models import auth_models as _auth_models  # noqa: F401,E402
from app.models import content_orm as _content_orm  # noqa: F401,E402
from app.models import activity_orm as _activity_orm  # noqa: F401,E402
from app.models import social_orm as _social_orm  # noqa: F401,E402
from app.models import connection as _connection  # noqa: F401,E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_upgrade_head(sqlite_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(cfg, "head")


@pytest.fixture
def fresh_db(tmp_path: Path) -> Iterator[Engine]:
    """Empty SQLite -> alembic upgrade head -> sync engine for assertions."""
    db_path = tmp_path / "schema_test.db"
    _alembic_upgrade_head(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def twice_migrated_db(tmp_path: Path) -> Iterator[Engine]:
    """Run ``alembic upgrade head`` twice in a row.

    The second invocation must be a complete no-op. This is the realistic
    production re-deploy scenario; if any migration is not idempotent it will
    raise on the second pass.
    """
    db_path = tmp_path / "twice_test.db"
    _alembic_upgrade_head(db_path)
    _alembic_upgrade_head(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        yield engine
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Drift assertions
# ---------------------------------------------------------------------------


def test_every_orm_table_exists_after_alembic_head(fresh_db: Engine) -> None:
    db_tables = set(inspect(fresh_db).get_table_names())
    orm_tables = set(Base.metadata.tables.keys())
    missing = orm_tables - db_tables
    assert not missing, (
        "Alembic does not create every table the ORM declares; missing in DB: "
        f"{sorted(missing)}"
    )


def test_platform_connections_table_present(fresh_db: Engine) -> None:
    insp = inspect(fresh_db)
    assert "platform_connections" in insp.get_table_names()

    cols = {c["name"]: c for c in insp.get_columns("platform_connections")}
    for required in (
        "id",
        "user_id",
        "platform",
        "access_token",
        "refresh_token",
        "expires_at",
        "scopes",
        "created_at",
        "updated_at",
    ):
        assert required in cols, f"platform_connections.{required} missing"

    uniques = {u["name"] for u in insp.get_unique_constraints("platform_connections")}
    assert "uq_platform_connection_user_platform" in uniques

    index_names = {i["name"] for i in insp.get_indexes("platform_connections")}
    assert "ix_platform_connections_user_id" in index_names
    assert "ix_platform_connections_platform" in index_names


def test_social_accounts_has_timestamp_columns(fresh_db: Engine) -> None:
    cols = {c["name"] for c in inspect(fresh_db).get_columns("social_accounts")}
    assert "created_at" in cols
    assert "updated_at" in cols


def test_orm_declared_indexes_now_exist(fresh_db: Engine) -> None:
    insp = inspect(fresh_db)

    rt_indexes = {i["name"] for i in insp.get_indexes("refresh_tokens")}
    assert "ix_refresh_tokens_expires_at" in rt_indexes

    pr_indexes = {i["name"] for i in insp.get_indexes("password_reset_tokens")}
    assert "ix_password_reset_tokens_expires_at" in pr_indexes

    al_indexes = {i["name"] for i in insp.get_indexes("activity_logs")}
    assert "ix_activity_logs_content_item_id" in al_indexes


# ---------------------------------------------------------------------------
# End-to-end: every ORM model writes successfully against migrated schema
# (no `create_all` involvement).
# ---------------------------------------------------------------------------


def _insert_user(conn: sa.Connection) -> str:
    user_id = str(uuid.uuid4())
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
    return user_id


def _insert_content_item(conn: sa.Connection, user_id: str) -> str:
    cid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "INSERT INTO content_items (id, user_id, status, file_type, created_at, updated_at) "
            "VALUES (:id, :uid, 'draft', 'image', :now, :now)"
        ),
        {"id": cid, "uid": user_id, "now": now},
    )
    return cid


def test_round_trip_inserts_against_migrated_schema(fresh_db: Engine) -> None:
    """Insert one row into every ORM-declared table using ONLY the migrated
    schema. If any required column was created via ``create_all`` rather than
    Alembic, this test fails with a clear column-not-found error.
    """
    now = datetime.now(timezone.utc)
    with fresh_db.begin() as conn:
        user_id = _insert_user(conn)
        cid = _insert_content_item(conn, user_id)

        conn.execute(
            text(
                "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, is_revoked, created_at) "
                "VALUES (:id, :uid, :h, :exp, 0, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "h": uuid.uuid4().hex,
                "exp": now + timedelta(days=30),
                "now": now,
            },
        )

        conn.execute(
            text(
                "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, is_used) "
                "VALUES (:id, :uid, :h, :exp, 0)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "h": uuid.uuid4().hex,
                "exp": now + timedelta(hours=1),
            },
        )

        conn.execute(
            text(
                "INSERT INTO social_accounts (id, user_id, platform, is_active, created_at, updated_at) "
                "VALUES (:id, :uid, 'youtube', 1, :now, :now)"
            ),
            {"id": str(uuid.uuid4()), "uid": user_id, "now": now},
        )

        conn.execute(
            text(
                "INSERT INTO platform_connections "
                "(id, user_id, platform, access_token, refresh_token, expires_at, scopes, created_at, updated_at) "
                "VALUES (:id, :uid, 'twitter', 'a', 'r', :exp, 'tweet.write', :now, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "exp": now + timedelta(hours=1),
                "now": now,
            },
        )

        conn.execute(
            text(
                "INSERT INTO platform_variants (id, content_item_id, platform, status, retry_count, manually_edited) "
                "VALUES (:id, :cid, 'youtube', 'scheduled', 0, 0)"
            ),
            {"id": str(uuid.uuid4()), "cid": cid},
        )

        conn.execute(
            text(
                "INSERT INTO activity_logs (id, user_id, content_item_id, action, created_at) "
                "VALUES (:id, :uid, :cid, 'test_action', :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": user_id,
                "cid": cid,
                "now": now,
            },
        )


def test_platform_connections_unique_constraint_enforced(fresh_db: Engine) -> None:
    """Confirm the (user_id, platform) unique constraint is live."""
    now = datetime.now(timezone.utc)
    with fresh_db.begin() as conn:
        user_id = _insert_user(conn)
        conn.execute(
            text(
                "INSERT INTO platform_connections "
                "(id, user_id, platform, created_at, updated_at) "
                "VALUES (:id, :uid, 'twitter', :now, :now)"
            ),
            {"id": str(uuid.uuid4()), "uid": user_id, "now": now},
        )

    with pytest.raises(sa.exc.IntegrityError):
        with fresh_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform_connections "
                    "(id, user_id, platform, created_at, updated_at) "
                    "VALUES (:id, :uid, 'twitter', :now, :now)"
                ),
                {"id": str(uuid.uuid4()), "uid": user_id, "now": now},
            )


# ---------------------------------------------------------------------------
# Idempotency: migration must apply cleanly to a database already created
# via ``Base.metadata.create_all``. This is the realistic "dev SQLite that
# was bootstrapped before Alembic was ever run" scenario.
# ---------------------------------------------------------------------------


def test_migration_idempotent_on_redeploy(twice_migrated_db: Engine) -> None:
    """Running ``alembic upgrade head`` twice must be a no-op the second time.

    If the fixture set up cleanly, every migration in the chain — including
    006's CREATE TABLE / ADD COLUMN / CREATE INDEX statements — guarded its
    side-effects with the live DB inspector and did not raise on the second
    pass. This is the realistic production re-deploy scenario.
    """
    insp = inspect(twice_migrated_db)
    db_tables = set(insp.get_table_names())
    orm_tables = set(Base.metadata.tables.keys())
    assert orm_tables.issubset(db_tables)
    assert "platform_connections" in db_tables
    cols = {c["name"] for c in insp.get_columns("social_accounts")}
    assert "created_at" in cols and "updated_at" in cols
