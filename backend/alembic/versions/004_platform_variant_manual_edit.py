"""platform_variants: manual edit audit columns

Revision ID: 004_platform_variant_manual_edit
Revises: 003_social_accounts_metadata
Create Date: 2026-05-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004_platform_variant_manual_edit"
down_revision = "003_social_accounts_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_variants",
        sa.Column("manually_edited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "platform_variants",
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_variants", "updated_at")
    op.drop_column("platform_variants", "manually_edited")
