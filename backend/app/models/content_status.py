"""Canonical ContentItem status taxonomy (B-6 fix).

This module is the single source of truth for the values that may appear
in ``content_items.status``. Both the application code and the Alembic
migration that defines the DB CHECK constraint import from here so the two
cannot drift again.

History (B-6 root cause): migration ``001_initial_schema`` allowed only
``draft, processing, scheduled, publishing, published, failed, assisted``.
The Celery worker wrote three additional terminal statuses that the constraint
did not allow (``completed``, ``completed_fallback``, ``error_fallback``).
On Postgres this caused every successful AI generation to fail with a
CheckViolation; the local SQLite dev database never carried the constraint
because tables there are bootstrapped via ``Base.metadata.create_all``, so the
bug was production-only and silent in dev.
"""

from __future__ import annotations

from enum import Enum
from typing import Final, FrozenSet


class ContentItemStatus(str, Enum):
    """All values legally permitted in ``content_items.status``.

    The values are kept stable across releases because they are persisted in
    the database. Adding a new value requires both a code change *and* a new
    Alembic migration extending the CHECK constraint.
    """

    # --- transient lifecycle states (set during processing) ---
    DRAFT = "draft"
    PROCESSING = "processing"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"

    # --- terminal AI generation outcomes (set by the content_processor) ---
    COMPLETED = "completed"
    COMPLETED_FALLBACK = "completed_fallback"
    ERROR_FALLBACK = "error_fallback"

    # --- terminal failure / manual-publishing states ---
    FAILED = "failed"
    ASSISTED = "assisted"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


CONTENT_ITEM_STATUSES: Final[FrozenSet[str]] = frozenset(ContentItemStatus.values())


def content_item_status_check_sql(column: str = "status") -> str:
    """Return the CHECK constraint expression for ``content_items.status``.

    The values are emitted in declaration order so the resulting SQL is stable
    and diff-friendly across migrations.
    """
    quoted = ",".join(f"'{v}'" for v in ContentItemStatus.values())
    return f"{column} IN ({quoted})"


__all__ = [
    "CONTENT_ITEM_STATUSES",
    "ContentItemStatus",
    "content_item_status_check_sql",
]
