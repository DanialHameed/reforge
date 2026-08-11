"""extend content_items.status CHECK constraint (B-6)

Revision ID: 005_content_items_status_extend
Revises: 004_platform_variant_manual_edit
Create Date: 2026-05-10

The previous CHECK constraint allowed:
  ('draft','processing','scheduled','publishing','published','failed','assisted')

But ``app/workers/content_processor.py`` writes three additional terminal
statuses on the happy path (``completed``, ``completed_fallback``,
``error_fallback``). Under PostgreSQL this caused a CheckViolation on every
successful AI generation. This migration aligns the database constraint with
the canonical Python enum (``app.models.content_status.ContentItemStatus``)
so the two can never drift again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Importing the canonical enum keeps the constraint and Python code in lockstep.
from app.models.content_status import content_item_status_check_sql

# revision identifiers, used by Alembic.
revision = "005_content_items_status_extend"
down_revision = "004_platform_variant_manual_edit"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_content_items_status"
TABLE_NAME = "content_items"

# Legacy expression preserved verbatim so the downgrade path restores the
# exact CHECK that was in place before this migration ran.
_LEGACY_CHECK_SQL = (
    "status IN ('draft','processing','scheduled','publishing',"
    "'published','failed','assisted')"
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _swap_check_constraint(new_check_sql: str) -> None:
    """Drop and recreate the CHECK constraint, dialect-aware.

    PostgreSQL gets native ``DROP CONSTRAINT IF EXISTS`` so the migration is
    idempotent against environments that may have been hand-patched. SQLite
    cannot drop CHECK constraints in place, so we use ``batch_alter_table``
    which transparently rebuilds the table. The drop is wrapped in a
    narrowly-scoped exception handler because dev SQLite databases that were
    bootstrapped via ``Base.metadata.create_all`` never had the original
    named constraint, and Alembic batch mode raises when asked to drop a
    constraint that is not present.
    """
    if _is_sqlite():
        with op.batch_alter_table(TABLE_NAME) as batch:
            try:
                batch.drop_constraint(CONSTRAINT_NAME, type_="check")
            except (KeyError, ValueError, sa.exc.InvalidRequestError):
                # Pre-existing dev DB without the named CHECK; safe to skip.
                pass
            batch.create_check_constraint(CONSTRAINT_NAME, new_check_sql)
    else:
        op.execute(
            f"ALTER TABLE {TABLE_NAME} DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
        )
        op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, new_check_sql)


def upgrade() -> None:
    _swap_check_constraint(content_item_status_check_sql())


def downgrade() -> None:
    _swap_check_constraint(_LEGACY_CHECK_SQL)
