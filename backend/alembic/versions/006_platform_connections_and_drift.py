"""B-7: backfill schema drift (platform_connections, social_accounts timestamps, missing indexes)

Revision ID: 006_platform_connections_and_drift
Revises: 005_content_items_status_extend
Create Date: 2026-05-10

Closes a long-standing drift between the ORM models and the Alembic schema:

* ``app/models/connection.py::PlatformConnection`` was never created by any
  migration. It only existed in production because ``app.main.lifespan``
  silently called ``Base.metadata.create_all()`` on every boot. Fresh Postgres
  deployments that ran ``alembic upgrade head`` only would not have the table
  at all, and OAuth callbacks (YouTube/Twitter/Meta/LinkedIn) would crash on
  first use.
* ``app/models/social_orm.py::SocialAccount`` has ``created_at`` and
  ``updated_at`` columns the initial migration never created.
* ORM-declared single-column indexes were missing from the migrations:
  ``refresh_tokens.expires_at``, ``password_reset_tokens.expires_at``, and
  ``activity_logs.content_item_id``.

The migration is intentionally **idempotent**: every CREATE/ADD checks the
live database via ``sa.inspect`` first. This is required because dev SQLite
databases bootstrapped via ``create_all`` may already contain some of these
objects, and the migration must apply cleanly there without raising.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006_platform_connections_and_drift"
down_revision = "005_content_items_status_extend"
branch_labels = None
depends_on = None


def _now_default() -> sa.text:
    return sa.text("CURRENT_TIMESTAMP")


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(i["name"] == name for i in sa.inspect(op.get_bind()).get_indexes(table))


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def _create_platform_connections_table() -> None:
    """Create ``platform_connections`` if absent.

    Schema mirrors ``app.models.connection.PlatformConnection`` exactly. The
    PK / FK columns use ``String(36)`` to match the cross-dialect storage
    pattern used by every other table created in migration 001 — the ORM's
    ``GUID`` type writes/reads strings on SQLite and round-trips uuid.UUID
    objects on Postgres against the same VARCHAR storage.
    """
    if _has_table("platform_connections"):
        return

    op.create_table(
        "platform_connections",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=_now_default(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=_now_default(),
        ),
        sa.UniqueConstraint(
            "user_id",
            "platform",
            name="uq_platform_connection_user_platform",
        ),
    )

    if not _has_index("platform_connections", "ix_platform_connections_user_id"):
        op.create_index(
            "ix_platform_connections_user_id",
            "platform_connections",
            ["user_id"],
            unique=False,
        )

    if not _has_index("platform_connections", "ix_platform_connections_platform"):
        op.create_index(
            "ix_platform_connections_platform",
            "platform_connections",
            ["platform"],
            unique=False,
        )

    # Postgres trigger so external SQL clients also bump updated_at; SQLAlchemy
    # ORM already issues the value on UPDATE via Column.onupdate=func.now().
    if _is_postgres():
        op.execute(
            """
            CREATE TRIGGER trg_platform_connections_set_updated_at
            BEFORE UPDATE ON platform_connections
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
            """
        )


def _add_social_accounts_timestamps() -> None:
    """Add ``social_accounts.created_at`` and ``updated_at`` if missing.

    ``server_default=now()`` ensures backfill of existing rows succeeds on a
    NOT NULL column. The ORM keeps ``server_default=func.now()`` so behavior
    is consistent with newly-created tables.
    """
    if _is_sqlite():
        if not _has_column("social_accounts", "created_at"):
            op.execute(
                "ALTER TABLE social_accounts "
                "ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        if not _has_column("social_accounts", "updated_at"):
            op.execute(
                "ALTER TABLE social_accounts "
                "ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        return

    # Postgres path
    if not _has_column("social_accounts", "created_at"):
        op.add_column(
            "social_accounts",
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=_now_default(),
            ),
        )
    if not _has_column("social_accounts", "updated_at"):
        op.add_column(
            "social_accounts",
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=_now_default(),
            ),
        )

    if _is_postgres():
        # Trigger may already exist on hand-patched envs; be idempotent.
        op.execute("DROP TRIGGER IF EXISTS trg_social_accounts_set_updated_at ON social_accounts;")
        op.execute(
            """
            CREATE TRIGGER trg_social_accounts_set_updated_at
            BEFORE UPDATE ON social_accounts
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
            """
        )


def _add_missing_indexes() -> None:
    """Add ORM-declared indexes the initial migration missed."""
    if not _has_index("refresh_tokens", "ix_refresh_tokens_expires_at"):
        op.create_index(
            "ix_refresh_tokens_expires_at",
            "refresh_tokens",
            ["expires_at"],
            unique=False,
        )

    if not _has_index("password_reset_tokens", "ix_password_reset_tokens_expires_at"):
        op.create_index(
            "ix_password_reset_tokens_expires_at",
            "password_reset_tokens",
            ["expires_at"],
            unique=False,
        )

    if not _has_index("activity_logs", "ix_activity_logs_content_item_id"):
        op.create_index(
            "ix_activity_logs_content_item_id",
            "activity_logs",
            ["content_item_id"],
            unique=False,
        )


def upgrade() -> None:
    _create_platform_connections_table()
    _add_social_accounts_timestamps()
    _add_missing_indexes()


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Reverse only what this migration created. Non-destructive on data
    in ``platform_connections`` would mean keeping the table; we drop it here
    because that is what the upgrade created. Operators that need to preserve
    OAuth state across a downgrade should snapshot the table beforehand.
    """
    if _has_index("activity_logs", "ix_activity_logs_content_item_id"):
        op.drop_index("ix_activity_logs_content_item_id", table_name="activity_logs")
    if _has_index("password_reset_tokens", "ix_password_reset_tokens_expires_at"):
        op.drop_index(
            "ix_password_reset_tokens_expires_at",
            table_name="password_reset_tokens",
        )
    if _has_index("refresh_tokens", "ix_refresh_tokens_expires_at"):
        op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")

    if _is_postgres():
        op.execute(
            "DROP TRIGGER IF EXISTS trg_social_accounts_set_updated_at ON social_accounts;"
        )

    if _has_column("social_accounts", "updated_at"):
        op.drop_column("social_accounts", "updated_at")
    if _has_column("social_accounts", "created_at"):
        op.drop_column("social_accounts", "created_at")

    if _is_postgres():
        op.execute(
            "DROP TRIGGER IF EXISTS trg_platform_connections_set_updated_at ON platform_connections;"
        )

    if _has_table("platform_connections"):
        if _has_index("platform_connections", "ix_platform_connections_platform"):
            op.drop_index(
                "ix_platform_connections_platform",
                table_name="platform_connections",
            )
        if _has_index("platform_connections", "ix_platform_connections_user_id"):
            op.drop_index(
                "ix_platform_connections_user_id",
                table_name="platform_connections",
            )
        op.drop_table("platform_connections")
