"""Canonical PlatformVariant status taxonomy (B-6 reinforcement).

Mirrors ``app.models.content_status`` but for ``platform_variants.status``.

History (this hardening pass): ``platform_variants.status`` was created
without any CHECK constraint, so a typo in any publisher (e.g.
``pv.status = "publised"``) would silently persist and immediately break
analytics / queue filters that compare against ``"published"`` literally.
This module is the single source of truth for the values that may appear
in ``platform_variants.status``; the migration that introduces the CHECK
constraint imports from here so the two cannot drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Final, FrozenSet


class PlatformVariantStatus(str, Enum):
    """All values legally permitted in ``platform_variants.status``."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ASSISTED = "assisted"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


PLATFORM_VARIANT_STATUSES: Final[FrozenSet[str]] = frozenset(
    PlatformVariantStatus.values()
)


def platform_variant_status_check_sql(column: str = "status") -> str:
    """Return the CHECK constraint expression for ``platform_variants.status``.

    Values are emitted in declaration order so the resulting SQL is stable
    and diff-friendly across migrations.
    """
    quoted = ",".join(f"'{v}'" for v in PlatformVariantStatus.values())
    return f"{column} IN ({quoted})"


__all__ = [
    "PLATFORM_VARIANT_STATUSES",
    "PlatformVariantStatus",
    "platform_variant_status_check_sql",
]
