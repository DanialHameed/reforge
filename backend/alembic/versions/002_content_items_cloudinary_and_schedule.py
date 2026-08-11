"""content_items cloudinary + scheduled_at

Revision ID: 002_content_items
Revises: 001_initial_schema
Create Date: 2026-04-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002_content_items"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_items", sa.Column("cloudinary_public_id", sa.Text(), nullable=True))
    op.add_column("content_items", sa.Column("cloudinary_resource_type", sa.String(length=20), nullable=True))
    op.add_column("content_items", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("content_items", "scheduled_at")
    op.drop_column("content_items", "cloudinary_resource_type")
    op.drop_column("content_items", "cloudinary_public_id")

