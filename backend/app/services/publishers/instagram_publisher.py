from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity_orm import ActivityLog
from app.models.content_orm import ContentItem, PlatformVariant
from app.models.social_orm import SocialAccount
from app.services.token_crypto import decrypt_token


class InstagramPublisher:
    """
    Instagram Graph API (Business/Creator) via Facebook Page token.

    Flow: container (media) -> media_publish; video/reels may require STATUS polling.
    """

    PLATFORM = "instagram"

    @property
    def _graph_base(self) -> str:
        v = (settings.META_GRAPH_VERSION or "v19.0").strip().lstrip("/")
        return f"https://graph.facebook.com/{v}"

    async def publish_feed_or_video(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        """Image or feed video (single)."""
        return await self._publish_impl(variant_id, db, as_reel=False)

    async def publish_reel(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        return await self._publish_impl(variant_id, db, as_reel=True)

    async def _publish_impl(self, variant_id: str, db: AsyncSession, *, as_reel: bool) -> dict[str, Any]:
        pv, sa, page_token, ig_user_id = await self._load_page_and_ig(variant_id, db)

        if not pv.media_url:
            raise ValueError("PlatformVariant.media_url is required for Instagram")

        caption = (pv.caption or "").strip()
        if pv.hashtags:
            tags = " ".join(h if str(h).startswith("#") else f"#{h}" for h in pv.hashtags)
            caption = f"{caption}\n\n{tags}".strip() if caption else tags

        u = (pv.media_url or "").lower().split("?")[0]
        is_image = any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))

        create_url = f"{self._graph_base}/{ig_user_id}/media"
        params: dict[str, Any] = {"caption": caption[:2200], "access_token": page_token}

        if is_image and not as_reel:
            params["image_url"] = pv.media_url
        else:
            params["media_type"] = "REELS" if as_reel else "VIDEO"
            params["video_url"] = pv.media_url

        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(create_url, params=params)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code >= 400:
                await self._fail(pv, sa, db, str(data))
                raise RuntimeError(data.get("error", {}).get("message", r.text))

        creation_id = str(data.get("id") or "")
        if not creation_id:
            raise RuntimeError("Instagram did not return creation id")

        # VIDEO/REELS often need processing — poll container status
        if not is_image or as_reel:
            await self._wait_for_container_ready(ig_user_id, creation_id, page_token, db, pv, sa)

        publish_url = f"{self._graph_base}/{ig_user_id}/media_publish"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r2 = await client.post(
                publish_url,
                params={"creation_id": creation_id, "access_token": page_token},
            )
            pub = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
            if r2.status_code >= 400:
                await self._fail(pv, sa, db, str(pub))
                raise RuntimeError(pub.get("error", {}).get("message", r2.text))

        media_id = str(pub.get("id") or "")
        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {
            **(pv.metadata_json or {}),
            "instagram_media_id": media_id,
            "is_reel": as_reel,
        }
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="instagram_published",
                details={"platform_variant_id": str(pv.id), "media_id": media_id, "reel": as_reel},
            )
        )
        await db.commit()
        return {"ok": True, "instagram_media_id": media_id}

    async def _wait_for_container_ready(
        self,
        ig_user_id: str,
        creation_id: str,
        page_token: str,
        db: AsyncSession,
        pv: PlatformVariant,
        sa: SocialAccount,
    ) -> None:
        """Poll GET /{ig-media-id}?fields=status_code until not PROCESSING (best-effort)."""
        url = f"{self._graph_base}/{creation_id}"
        for _ in range(60):
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, params={"fields": "status_code", "access_token": page_token})
                if r.status_code >= 400:
                    return
                data = r.json()
            code = data.get("status_code")
            if code in (None, "FINISHED", "PUBLISHED"):
                return
            if code == "ERROR":
                await self._fail(pv, sa, db, str(data))
                raise RuntimeError("Instagram media container failed processing")
            await asyncio.sleep(2)
        # Time out — still try publish; Graph may accept
        return

    async def _load_page_and_ig(self, variant_id: str, db: AsyncSession) -> tuple[PlatformVariant, SocialAccount, str, str]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != self.PLATFORM:
            raise ValueError("Not an Instagram variant")

        item = (await db.execute(select(ContentItem).where(ContentItem.id == pv.content_item_id))).scalar_one_or_none()
        if item is None:
            raise ValueError("ContentItem not found")

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
            raise ValueError("No active Instagram SocialAccount for user")

        user_token = decrypt_token(sa.access_token_encrypted)
        if not user_token:
            raise ValueError("Missing Meta user token for Instagram")

        # Page token + IG user id (cached in metadata)
        meta = sa.metadata_json or {}
        page_id = meta.get("page_id")
        ig_id = meta.get("instagram_business_account_id")

        if not page_id or not ig_id:
            page_token, page_id_resolved, ig_resolved = await self._fetch_page_and_ig(user_token)
            page_id = page_id or page_id_resolved
            ig_id = ig_id or ig_resolved
            meta["page_id"] = page_id
            meta["instagram_business_account_id"] = ig_id
            sa.metadata_json = meta
            db.add(sa)
            await db.commit()
        else:
            page_token = await self._get_page_token_for_page(user_token, str(page_id))

        return pv, sa, page_token, str(ig_id)

    async def _fetch_page_and_ig(self, user_token: str) -> tuple[str, str, str]:
        url = f"{self._graph_base}/me/accounts"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params={"access_token": user_token})
            r.raise_for_status()
            data = r.json()
        pages = data.get("data") or []
        if not pages:
            raise ValueError("No Facebook Pages for Instagram; connect a Page linked to IG Professional.")

        # Prefer a page that has a linked Instagram Business account.
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for page in pages:
                page_id = str(page.get("id") or "")
                page_token = str(page.get("access_token") or "")
                if not page_id or not page_token:
                    continue
                r2 = await client.get(
                    f"{self._graph_base}/{page_id}",
                    params={"fields": "instagram_business_account", "access_token": page_token},
                )
                if r2.status_code != 200:
                    continue
                ig = (r2.json() or {}).get("instagram_business_account") or {}
                ig_id = str(ig.get("id") or "")
                if ig_id:
                    return page_token, page_id, ig_id

        raise ValueError(
            "No linked Instagram Business account found on any available Facebook Page. "
            "Connect an Instagram Professional account to a Facebook Page, then select that Page in Connections."
        )

    async def _get_page_token_for_page(self, user_token: str, page_id: str) -> str:
        url = f"{self._graph_base}/me/accounts"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params={"access_token": user_token})
            r.raise_for_status()
            for p in r.json().get("data") or []:
                if str(p.get("id")) == page_id:
                    return str(p.get("access_token") or "")
        raise ValueError("Could not resolve page access token for Instagram")

    async def _fail(self, pv: PlatformVariant, sa: SocialAccount, db: AsyncSession, msg: str) -> None:
        pv.status = "failed"
        pv.error_message = msg[:2000]
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="instagram_publish_failed",
                details={"message": msg[:500]},
            )
        )
        await db.commit()
