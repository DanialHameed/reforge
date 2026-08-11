"""Data access repositories (async SQLAlchemy)."""

from app.repositories.content_repository import content_repository
from app.repositories.platform_variant_repository import (
    canonical_platform_key,
    platform_variant_repository,
)

__all__ = [
    "content_repository",
    "platform_variant_repository",
    "canonical_platform_key",
]
