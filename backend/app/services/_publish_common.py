"""
Shared building blocks for the per-platform "direct" publisher services
(`app/services/{youtube,twitter,facebook,instagram,linkedin}_publisher.py`).

These services follow the same shape as `upload_video_to_youtube`:
    async def upload_*_to_<platform>(user_id, content_id) -> dict[str, Any]

They all read tokens from `PlatformConnection`, look up the matching
`PlatformVariant` for the platform, and update its status in place.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_orm import ActivityLog
from app.models.connection import PlatformConnection
from app.models.content_orm import ContentItem, PlatformVariant

logger = logging.getLogger(__name__)


# Captions / titles that ReForge falls back to when AI generation fails.
# We block publishing on these to avoid spamming the user's followers with
# obviously generic content.
GENERIC_FALLBACK_SIGNALS = (
    "amazing content worth sharing",
    "just dropped some amazing content",
    "check out this amazing content",
    "excited to share this piece of content with my network. quality work speaks for itself",
    "amazing content you need to see",
    "incredible content created with reforge",
    "like and subscribe for more",
)


def is_fallback_text(*pieces: str | None) -> bool:
    blob = " ".join((p or "").lower() for p in pieces).strip()
    if not blob:
        return False
    return any(sig in blob for sig in GENERIC_FALLBACK_SIGNALS)


async def load_connection(db: AsyncSession, *, user_id: UUID, platform: str) -> PlatformConnection | None:
    return (
        await db.execute(
            select(PlatformConnection).where(
                PlatformConnection.user_id == user_id,
                PlatformConnection.platform == platform,
            )
        )
    ).scalar_one_or_none()


async def load_content(db: AsyncSession, *, user_id: UUID, content_id: UUID) -> ContentItem | None:
    return (
        await db.execute(
            select(ContentItem).where(
                ContentItem.id == content_id,
                ContentItem.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def load_variant(
    db: AsyncSession,
    *,
    content_item_id: UUID,
    platform: str,
) -> PlatformVariant | None:
    return (
        await db.execute(
            select(PlatformVariant).where(
                PlatformVariant.content_item_id == content_item_id,
                PlatformVariant.platform == platform,
            )
        )
    ).scalar_one_or_none()


async def mark_publishing(db: AsyncSession, pv: PlatformVariant) -> None:
    pv.status = "publishing"
    pv.error_message = None
    db.add(pv)
    await db.commit()


async def mark_published(
    db: AsyncSession,
    pv: PlatformVariant,
    *,
    extra_metadata: dict[str, Any] | None = None,
    user_id: UUID | None = None,
    log_action: str | None = None,
    log_details: dict[str, Any] | None = None,
) -> None:
    pv.status = "published"
    pv.error_message = None
    pv.published_at = datetime.now(timezone.utc)
    if extra_metadata:
        pv.metadata_json = {**(pv.metadata_json or {}), **extra_metadata}
    db.add(pv)
    if log_action and user_id is not None:
        db.add(
            ActivityLog(
                user_id=user_id,
                content_item_id=pv.content_item_id,
                action=log_action,
                details=log_details or {},
            )
        )
    await db.commit()


async def mark_failed(
    db: AsyncSession,
    pv: PlatformVariant,
    *,
    error: str,
    user_id: UUID | None = None,
    log_action: str | None = None,
    log_details: dict[str, Any] | None = None,
) -> None:
    pv.status = "failed"
    pv.error_message = error[:500]
    pv.retry_count = (pv.retry_count or 0) + 1
    db.add(pv)
    if log_action and user_id is not None:
        db.add(
            ActivityLog(
                user_id=user_id,
                content_item_id=pv.content_item_id,
                action=log_action,
                details=log_details or {"error": error},
            )
        )
    await db.commit()


def variant_meta(pv: PlatformVariant) -> dict[str, Any]:
    if isinstance(pv.metadata_json, dict):
        return pv.metadata_json
    return {}


def compose_caption(caption: str | None, hashtags: list[str] | None, *, max_tags: int | None = None) -> str:
    cap = (caption or "").strip()
    tags: list[str] = []
    if hashtags:
        tags = [h if str(h).startswith("#") else f"#{h}" for h in hashtags if str(h).strip()]
        if max_tags is not None:
            tags = tags[:max_tags]
    if not tags:
        return cap
    if not cap:
        return " ".join(tags)
    return f"{cap}\n\n{' '.join(tags)}"
