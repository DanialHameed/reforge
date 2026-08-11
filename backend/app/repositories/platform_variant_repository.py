from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content_orm import PlatformVariant


def canonical_platform_key(platform: str | None) -> str:
    """Normalize URL segment / DB value (`x` ≡ `twitter`)."""
    n = (platform or "").strip().lower()
    if n == "x":
        return "twitter"
    return n


def extract_caption_from_patch_data(data: dict[str, Any]) -> str | None:
    for key in ("caption", "tweet", "post", "title"):
        v = data.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s != "":
            return s
    return None


class PlatformVariantRepository:
    async def update_variant_data(
        self,
        db: AsyncSession,
        *,
        content_id: uuid.UUID,
        platform: str,
        new_data: dict[str, Any],
        manually_edited: bool = False,
    ) -> bool:
        stmt = (
            select(PlatformVariant)
            .where(PlatformVariant.content_item_id == content_id)
            .order_by(PlatformVariant.id.asc())
        )
        rows = (await db.execute(stmt)).scalars().all()

        norm = canonical_platform_key(platform)
        variant: PlatformVariant | None = None
        for v in rows:
            if canonical_platform_key(v.platform) == norm:
                variant = v
                break

        if variant is None:
            return False

        new_caption = extract_caption_from_patch_data(new_data)
        if new_caption is not None:
            variant.caption = new_caption

        if "hashtags" in new_data:
            tags = new_data.get("hashtags")
            if tags is None:
                variant.hashtags = None
            elif isinstance(tags, list):
                variant.hashtags = [str(t) for t in tags]

        if variant.platform and str(variant.platform).lower().strip() == "youtube":
            md = dict(variant.metadata_json or {})
            if "description" in new_data and isinstance(new_data.get("description"), str):
                md["description"] = str(new_data["description"])
                variant.metadata_json = md

        variant.manually_edited = manually_edited
        variant.updated_at = datetime.now(timezone.utc)

        db.add(variant)
        await db.commit()
        await db.refresh(variant)
        return True


platform_variant_repository = PlatformVariantRepository()
