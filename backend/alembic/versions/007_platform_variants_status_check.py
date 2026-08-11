"""add CHECK constraint to platform_variants.status (B-6 reinforcement)

Revision ID: 007_platform_variants_status_check
Revises: 006_platform_connections_and_drift
Create Date: 2026-05-11

The initial schema (``001_initial_schema``) created
``platform_variants.status`` as an unconstrained ``String(50)``. Every
publisher writes one of a small, well-known set of values (``draft``,
``scheduled``, ``publishing``, ``published``, ``failed``, ``assisted``)
but a typo in any of them — for instance ``pv.status = "publised"`` —
would silently persist and break the analytics queries that compare
against the ``"published"`` literal.

This migration aligns the database with the canonical Python enum
``app.models.platform_variant_status.PlatformVariantStatus`` so the two
can never drift again. It is structured the same way as the
``content_items.status`` extension in migration 005 (idempotent
DROP-then-ADD on Postgres, ``batch_alter_table`` on SQLite).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.platform_variant_status import platform_variant_status_check_sql

revision = "007_platform_variants_status_check"
down_revision = "006_platform_connections_and_drift"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_platform_variants_status"
TABLE_NAME = "platform_variants"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _sqlite_has_named_check(table: str, name: str) -> bool:
    """Return True iff a CHECK with ``name`` is present on ``table`` in SQLite.

    SQLite stores CHECK constraints inline in the ``CREATE TABLE`` SQL kept
    in ``sqlite_master``. There is no constraint catalog like Postgres'
    ``pg_constraint``, so we string-match the constraint name in the stored
    DDL. Quote-style varies (``"name"`` vs. unquoted) so we accept both.
    """
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"
        ),
        {"t": table},
    ).fetchone()
    if not row or not row[0]:
        return False
    sql = str(row[0])
    return f'"{name}"' in sql or f" {name} " in sql or f" {name}\n" in sql


def _swap_check_constraint(new_check_sql: str | None) -> None:
    """Drop and (optionally) recreate the CHECK constraint, dialect-aware.

    ``new_check_sql=None`` removes the constraint entirely (used by
    ``downgrade``).

    SQLite quirk: ``batch_alter_table`` queues operations and applies them
    when the context exits, so a missing constraint raises ``ValueError``
    *outside* the ``with`` block. We must therefore introspect first and
    only schedule the drop when the constraint actually exists.
    """
    if _is_sqlite():
        had_constraint = _sqlite_has_named_check(TABLE_NAME, CONSTRAINT_NAME)
        with op.batch_alter_table(TABLE_NAME) as batch:
            if had_constraint:
                batch.drop_constraint(CONSTRAINT_NAME, type_="check")
            if new_check_sql is not None:
                batch.create_check_constraint(CONSTRAINT_NAME, new_check_sql)
    else:
        op.execute(
            f"ALTER TABLE {TABLE_NAME} DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
        )
        if new_check_sql is not None:
            op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, new_check_sql)


def upgrade() -> None:
    _swap_check_constraint(platform_variant_status_check_sql())


def downgrade() -> None:
    # The pre-007 schema had no CHECK on this column.
    _swap_check_constraint(None)
