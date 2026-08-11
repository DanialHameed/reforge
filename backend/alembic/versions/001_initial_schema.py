"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _uuid_col(name: str, primary_key: bool = False, nullable: bool = False):
    # Use a simple String UUID for SQLite compatibility.
    return sa.Column(name, sa.String(length=36), primary_key=primary_key, nullable=nullable)


def _now_default() -> sa.text:
    # Works on SQLite and Postgres
    return sa.text("CURRENT_TIMESTAMP")


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("plan", sa.String(length=20), nullable=False, server_default=sa.text("'free'")),
        sa.Column(
            "llm_migration_feature_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
        sa.CheckConstraint("plan IN ('free','premium')", name="ck_users_plan"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    # --- refresh_tokens ---
    op.create_table(
        "refresh_tokens",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=False)

    # --- password_reset_tokens ---
    op.create_table(
        "password_reset_tokens",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"], unique=False)
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=False)

    # --- social_accounts ---
    op.create_table(
        "social_accounts",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("platform_user_id", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "platform IN ('youtube','instagram','facebook','twitter','linkedin')",
            name="ck_social_accounts_platform",
        ),
    )
    op.create_index(
        "ix_social_accounts_user_id_platform",
        "social_accounts",
        ["user_id", "platform"],
        unique=False,
    )

    # --- content_items ---
    op.create_table(
        "content_items",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("original_file_url", sa.Text(), nullable=True),
        sa.Column("file_type", sa.String(length=50), nullable=True),
        sa.Column("file_size_mb", sa.Numeric(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
        sa.CheckConstraint("file_type IN ('video','image','article')", name="ck_content_items_file_type"),
        sa.CheckConstraint(
            "status IN ('draft','processing','scheduled','publishing','published','failed','assisted')",
            name="ck_content_items_status",
        ),
    )
    op.create_index(
        "ix_content_items_user_id_status",
        "content_items",
        ["user_id", "status"],
        unique=False,
    )

    # --- platform_variants ---
    op.create_table(
        "platform_variants",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column(
            "content_item_id",
            sa.String(length=36),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("hashtags", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_platform_variants_content_item_id", "platform_variants", ["content_item_id"], unique=False)

    # --- activity_logs ---
    op.create_table(
        "activity_logs",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "content_item_id",
            sa.String(length=36),
            sa.ForeignKey("content_items.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
    )
    # Index with DESC ordering (Postgres-specific)
    if _is_sqlite():
        op.create_index("ix_activity_logs_user_id_created_at", "activity_logs", ["user_id", "created_at"], unique=False)
    else:
        op.execute("CREATE INDEX ix_activity_logs_user_id_created_at_desc ON activity_logs (user_id, created_at DESC);")

    # --- llm_migration_jobs ---
    op.create_table(
        "llm_migration_jobs",
        _uuid_col("id", primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column("sql_generated", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=255), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=_now_default()),
    )

    # --- updated_at auto-update trigger (users, content_items) ---
    if not _is_sqlite():
        op.execute(
            """
            CREATE OR REPLACE FUNCTION set_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
              NEW.updated_at = now();
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )

        op.execute(
            """
            CREATE TRIGGER trg_users_set_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_content_items_set_updated_at
            BEFORE UPDATE ON content_items
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at();
            """
        )


def downgrade() -> None:
    if not _is_sqlite():
        op.execute("DROP TRIGGER IF EXISTS trg_content_items_set_updated_at ON content_items;")
        op.execute("DROP TRIGGER IF EXISTS trg_users_set_updated_at ON users;")
        op.execute("DROP FUNCTION IF EXISTS set_updated_at();")

        op.execute("DROP INDEX IF EXISTS ix_activity_logs_user_id_created_at_desc;")
    else:
        op.drop_index("ix_activity_logs_user_id_created_at", table_name="activity_logs")

    op.drop_index("ix_platform_variants_content_item_id", table_name="platform_variants")
    op.drop_table("platform_variants")

    op.drop_index("ix_content_items_user_id_status", table_name="content_items")
    op.drop_table("content_items")

    op.drop_index("ix_social_accounts_user_id_platform", table_name="social_accounts")
    op.drop_table("social_accounts")

    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_table("activity_logs")
    op.drop_table("llm_migration_jobs")

