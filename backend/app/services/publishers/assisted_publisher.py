from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_orm import ActivityLog
from app.models.content_orm import ContentItem, PlatformVariant


class AssistedPackage(BaseModel):
    platform: Literal["instagram", "linkedin"]
    posting_package: dict[str, Any]


def _format_hashtags(hashtags: list[str] | None) -> str:
    if not hashtags:
        return ""
    out: list[str] = []
    for h in hashtags:
        hs = str(h).strip()
        if not hs:
            continue
        out.append(hs if hs.startswith("#") else f"#{hs}")
    return " ".join(out)


def _cloudinary_transform(url: str, aspect: Literal["1:1", "9:16"]) -> str:
    """
    Best-effort Cloudinary transformation insertion.
    If URL isn't Cloudinary, return unchanged.
    """
    if "res.cloudinary.com" not in url or "/upload/" not in url:
        return url
    if aspect == "1:1":
        t = "c_fill,ar_1:1,g_auto"
    else:
        t = "c_fill,ar_9:16,g_auto"
    return url.replace("/upload/", f"/upload/{t}/", 1)


class AssistedPublisher:
    async def prepare_instagram_assisted(self, variant_id: str, db: AsyncSession) -> AssistedPackage:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != "instagram":
            raise ValueError("Variant is not instagram")

        pv.status = "assisted"
        db.add(pv)

        caption = (pv.caption or "").strip()
        hashtags_formatted = _format_hashtags(pv.hashtags)
        combined_text = (caption + ("\n\n" + hashtags_formatted if hashtags_formatted else "")).strip()

        media_url = pv.media_url or ""
        posting_package = {
            "platform": "instagram",
            "caption": caption,
            "hashtags_formatted": hashtags_formatted,
            "combined_text": combined_text,
            "media_url_feed": _cloudinary_transform(media_url, "1:1") if media_url else None,
            "media_url_reel": _cloudinary_transform(media_url, "9:16") if media_url else None,
            "deep_link": "https://www.instagram.com/",
            "instructions": [
                "1. Open Instagram",
                "2. Tap + to create post",
                "3. Select your media from camera roll",
                "4. Paste the caption (pre-copied to clipboard)",
                "5. Tap Share",
            ],
            "copy_text": combined_text,
        }

        db.add(
            ActivityLog(
                user_id=None,
                content_item_id=pv.content_item_id,
                action="assisted_instagram_prepared",
                details={"platform_variant_id": str(pv.id), "posting_package": posting_package},
            )
        )
        await db.commit()
        return AssistedPackage(platform="instagram", posting_package=posting_package)

    async def prepare_linkedin_assisted(self, variant_id: str, db: AsyncSession) -> AssistedPackage:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != "linkedin":
            raise ValueError("Variant is not linkedin")

        pv.status = "assisted"
        db.add(pv)

        post = (pv.caption or "").strip()
        hashtags_formatted = _format_hashtags(pv.hashtags)
        combined_text = (post + ("\n\n" + hashtags_formatted if hashtags_formatted else "")).strip()

        posting_package = {
            "platform": "linkedin",
            "post": post,
            "hashtags_formatted": hashtags_formatted,
            "combined_text": combined_text,
            "media_url": pv.media_url,
            "deep_link": "https://www.linkedin.com/post/new",
            "instructions": [
                "1. Open LinkedIn",
                "2. Start a new post",
                "3. Attach your media (if any)",
                "4. Paste the post text",
                "5. Click Post",
            ],
            "copy_text": combined_text,
        }

        db.add(
            ActivityLog(
                user_id=None,
                content_item_id=pv.content_item_id,
                action="assisted_linkedin_prepared",
                details={"platform_variant_id": str(pv.id), "posting_package": posting_package},
            )
        )
        await db.commit()
        return AssistedPackage(platform="linkedin", posting_package=posting_package)

    async def mark_as_published(self, variant_id: str, db: AsyncSession, confirmed_at: datetime) -> dict[str, Any]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")

        pv.status = "published"
        pv.published_at = confirmed_at
        pv.error_message = None
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=None,
                content_item_id=pv.content_item_id,
                action="assisted_confirmed_published",
                details={"platform_variant_id": str(pv.id), "confirmed_at": confirmed_at.isoformat()},
            )
        )
        await db.commit()
        return {"ok": True}

    async def get_upcoming_assisted(self, user_id: str, db: AsyncSession) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=2)

        # Join ContentItem to filter by user.
        stmt = (
            select(PlatformVariant, ContentItem)
            .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
            .where(
                ContentItem.user_id == user_id,
                PlatformVariant.status == "assisted",
                PlatformVariant.scheduled_at.is_not(None),
                PlatformVariant.scheduled_at >= now,
                PlatformVariant.scheduled_at <= end,
            )
            .order_by(PlatformVariant.scheduled_at.asc())
        )

        rows = (await db.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for pv, item in rows:
            out.append(
                {
                    "id": str(pv.id),
                    "platform": pv.platform,
                    "content_item_id": str(pv.content_item_id),
                    "scheduled_at": pv.scheduled_at.isoformat() if pv.scheduled_at else None,
                    "caption": pv.caption,
                    "hashtags": pv.hashtags,
                    "media_url": pv.media_url,
                }
            )
        return out

