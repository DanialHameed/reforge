"""add social_accounts metadata json

Revision ID: 003_social_accounts_metadata
Revises: 002_content_items
Create Date: 2026-05-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_social_accounts_metadata"
down_revision = "002_content_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_accounts", sa.Column("metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("social_accounts", "metadata")

