from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity_orm import ActivityLog
from app.models.content_orm import ContentItem, PlatformVariant
from app.models.social_orm import SocialAccount
from app.services.publishers.errors import RetryablePublishError
from app.services.token_crypto import decrypt_token

__all__ = ["RetryablePublishError", "FacebookPublisher"]


class FacebookPublisher:
    PLATFORM = "facebook"

    @property
    def GRAPH_BASE(self) -> str:  # noqa: N802
        v = (settings.META_GRAPH_VERSION or "v19.0").strip().lstrip("/")
        return f"https://graph.facebook.com/{v}"

    async def publish_photo(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa, user_token = await self._load_variant_and_account(variant_id, db)
        page_id, page_name, page_token = await self._get_page_context(user_token=user_token, sa=sa, db=db)

        caption = self._compose_caption(pv.caption, pv.hashtags)
        url = f"{self.GRAPH_BASE}/{page_id}/photos"
        params = {"url": pv.media_url, "caption": caption, "access_token": page_token}

        data = await self._graph_post(url, params=params, db=db, sa=sa, pv=pv)
        photo_id = str(data.get("id") or "")
        await self._mark_published(pv, db, sa, external_id=photo_id, page_name=page_name)
        return {"ok": True, "photo_id": photo_id}

    async def publish_video(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa, user_token = await self._load_variant_and_account(variant_id, db)
        page_id, page_name, page_token = await self._get_page_context(user_token=user_token, sa=sa, db=db)

        title = (pv.caption or "").strip()[:255] or "ReForge Video"
        description = self._compose_caption(pv.caption, pv.hashtags)

        url = f"{self.GRAPH_BASE}/{page_id}/videos"
        params = {
            "title": title,
            "description": description,
            "file_url": pv.media_url,
            "access_token": page_token,
        }

        data = await self._graph_post(url, params=params, db=db, sa=sa, pv=pv)
        video_id = str(data.get("id") or data.get("video_id") or "")
        await self._mark_published(pv, db, sa, external_id=video_id, page_name=page_name)
        return {"ok": True, "video_id": video_id}

    async def publish_reel(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa, user_token = await self._load_variant_and_account(variant_id, db)
        page_id, page_name, page_token = await self._get_page_context(user_token=user_token, sa=sa, db=db)

        description = self._compose_caption(pv.caption, pv.hashtags)

        # Facebook Reels endpoint (Graph): /{page-id}/video_reels
        url = f"{self.GRAPH_BASE}/{page_id}/video_reels"
        params = {
            "video_url": pv.media_url,
            "description": description,
            "access_token": page_token,
        }

        data = await self._graph_post(url, params=params, db=db, sa=sa, pv=pv)
        reel_id = str(data.get("id") or "")
        await self._mark_published(pv, db, sa, external_id=reel_id, page_name=page_name)
        return {"ok": True, "reel_id": reel_id}

    async def get_page_access_token(self, user_access_token: str) -> str:
        # Page token comes from /me/accounts response (requires pages_show_list, pages_read_engagement, etc.)
        url = f"{self.GRAPH_BASE}/me/accounts"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, params={"access_token": user_access_token})
            r.raise_for_status()
            data = r.json()
        pages = data.get("data") or []
        if not pages:
            raise ValueError("No Facebook Pages found for this user.")
        token = pages[0].get("access_token")
        if not token:
            raise ValueError("Missing page access token from /me/accounts.")
        return str(token)

    async def get_page_id(self, access_token: str) -> tuple[str, str]:
        url = f"{self.GRAPH_BASE}/me/accounts"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, params={"access_token": access_token})
            r.raise_for_status()
            data = r.json()
        pages = data.get("data") or []
        if not pages:
            raise ValueError("No Facebook Pages found for this user.")
        page_id = str(pages[0].get("id") or "")
        page_name = str(pages[0].get("name") or "")
        if not page_id:
            raise ValueError("Missing page id from /me/accounts.")
        return page_id, page_name

    # -------------------------
    # Internals
    # -------------------------

    async def _load_variant_and_account(self, variant_id: str, db: AsyncSession) -> tuple[PlatformVariant, SocialAccount, str]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != self.PLATFORM:
            raise ValueError("PlatformVariant is not a Facebook variant")
        if not pv.media_url:
            raise ValueError("PlatformVariant.media_url is required")

        item = (await db.execute(select(ContentItem).where(ContentItem.id == pv.content_item_id))).scalar_one_or_none()
        if item is None:
            raise ValueError("ContentItem not found for variant")

        sa = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.user_id == item.user_id,
                    SocialAccount.platform == self.PLATFORM,
                    SocialAccount.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if sa is None:
            raise ValueError("No active Facebook SocialAccount connected for user")

        user_token = decrypt_token(sa.access_token_encrypted)
        if not user_token:
            raise ValueError("Missing Facebook user access token")
        return pv, sa, user_token

    async def _get_page_context(self, *, user_token: str, sa: SocialAccount, db: AsyncSession) -> tuple[str, str, str]:
        # If we already cached page_id, reuse it; otherwise resolve deterministically from /me/accounts.
        meta = sa.metadata_json or {}
        preferred_page_id = str(meta.get("page_id") or "").strip() if isinstance(meta, dict) else ""

        # Pull page list once, then select.
        url = f"{self.GRAPH_BASE}/me/accounts"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, params={"access_token": user_token})
            r.raise_for_status()
            pages = r.json().get("data") or []

        if not pages:
            raise ValueError("No Facebook Pages found for this user.")

        chosen = None
        if preferred_page_id:
            for p in pages:
                if str(p.get("id") or "") == preferred_page_id:
                    chosen = p
                    break
            if chosen is None:
                raise ValueError(
                    "Selected Facebook Page is no longer available for this account. "
                    "Please re-select a Page in Connections."
                )
        else:
            chosen = pages[0]

        page_id = str(chosen.get("id") or "")
        page_name = str(chosen.get("name") or "")
        page_token = str(chosen.get("access_token") or "")
        if not page_id or not page_token:
            raise ValueError("Missing page id or page access token from /me/accounts.")

        meta["page_id"] = page_id
        meta["page_name"] = page_name
        sa.metadata_json = meta
        db.add(sa)
        await db.commit()
        return page_id, page_name, page_token

    def _compose_caption(self, caption: str | None, hashtags: list[str] | None) -> str:
        cap = (caption or "").strip()
        tags = []
        if hashtags:
            tags = [h if h.startswith("#") else f"#{h}" for h in hashtags][:3]
        if tags:
            if cap:
                return cap + "\n\n" + " ".join(tags)
            return " ".join(tags)
        return cap

    async def _graph_post(
        self,
        url: str,
        *,
        params: dict[str, Any],
        db: AsyncSession,
        sa: SocialAccount,
        pv: PlatformVariant,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.post(url, data=params)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}

        if r.status_code >= 400:
            await self._handle_graph_error(data, db=db, sa=sa, pv=pv)
            # If handler didn't raise, raise generic.
            raise RuntimeError(f"Facebook Graph API error ({r.status_code})")

        return data

    async def _handle_graph_error(self, data: dict, *, db: AsyncSession, sa: SocialAccount, pv: PlatformVariant) -> None:
        err = (data or {}).get("error") or {}
        code = err.get("code")
        subcode = err.get("error_subcode")
        message = str(err.get("message") or "Facebook error")

        # OAuthException token expired/invalid
        if code == 190:
            sa.is_active = False
            db.add(sa)
            pv.status = "failed"
            pv.error_message = "Facebook token expired. Please reconnect your account."
            db.add(pv)
            db.add(
                ActivityLog(
                    user_id=sa.user_id,
                    content_item_id=pv.content_item_id,
                    action="facebook_token_expired",
                    details={"code": code, "subcode": subcode, "message": message},
                )
            )
            await db.commit()
            return

        # Missing permission
        if code == 200:
            pv.status = "failed"
            pv.error_message = f"Missing Facebook permissions: {message}"
            db.add(pv)
            db.add(
                ActivityLog(
                    user_id=sa.user_id,
                    content_item_id=pv.content_item_id,
                    action="facebook_permission_missing",
                    details={"code": code, "subcode": subcode, "message": message},
                )
            )
            await db.commit()
            return

        # Rate limit
        if code == 4:
            db.add(
                ActivityLog(
                    user_id=sa.user_id,
                    content_item_id=pv.content_item_id,
                    action="facebook_rate_limited",
                    details={"code": code, "subcode": subcode, "message": message, "retry_after_seconds": 3600},
                )
            )
            await db.commit()
            raise RetryablePublishError("Facebook rate limit. Retry later.", retry_after_seconds=3600)

        pv.status = "failed"
        pv.error_message = f"Facebook error: {message}"
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="facebook_publish_failed",
                details={"code": code, "subcode": subcode, "message": message},
            )
        )
        await db.commit()

    async def _mark_published(
        self,
        pv: PlatformVariant,
        db: AsyncSession,
        sa: SocialAccount,
        *,
        external_id: str,
        page_name: str,
    ) -> None:
        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {**(pv.metadata_json or {}), "facebook_id": external_id, "page_name": page_name}
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="facebook_published",
                details={"platform_variant_id": str(pv.id), "facebook_id": external_id, "page_name": page_name},
            )
        )
        await db.commit()

