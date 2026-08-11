from __future__ import annotations

import mimetypes
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity_orm import ActivityLog
from app.models.content_orm import ContentItem, PlatformVariant
from app.models.social_orm import SocialAccount
from app.services.token_crypto import decrypt_token, encrypt_token


class LinkedInPublisher:
    """Native LinkedIn UGC post (v2) using OAuth 2.0 user access token."""

    PLATFORM = "linkedin"
    API_BASE = "https://api.linkedin.com/v2"

    async def publish_post(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa = await self._load_variant_and_account(variant_id, db)
        access_token = await self._ensure_access_token(sa, db)

        person_urn = self._person_urn(sa)
        if not person_urn:
            raise ValueError(
                "Missing LinkedIn person URN. Store under social_accounts.metadata "
                "as person_urn or linkedin_person_urn (e.g. urn:li:person:xxxx)."
            )

        caption = (pv.caption or "").strip()
        tags = []
        if pv.hashtags:
            tags = [h if str(h).startswith("#") else f"#{h}" for h in pv.hashtags]
        hashtags_line = " ".join(tags) if tags else ""
        body_text = caption
        if hashtags_line:
            body_text = f"{caption}\n\n{hashtags_line}".strip()

        headers = {
            "authorization": f"Bearer {access_token}",
            "x-restli-protocol-version": "2.0.0",
            "content-type": "application/json",
        }

        share_content: dict[str, Any] = {
            "shareCommentary": {"text": body_text[:3000]},
            "shareMediaCategory": "NONE",
        }

        # Native media upload (registerUpload -> upload -> reference asset)
        if pv.media_url:
            asset_urn, media_category = await self._upload_media_asset(
                access_token=access_token,
                owner_urn=person_urn,
                media_url=str(pv.media_url),
            )
            share_content["shareMediaCategory"] = media_category
            share_content["media"] = [
                {
                    "status": "READY",
                    "media": asset_urn,
                    "title": {"text": (caption or "ReForge").strip()[:200] or "ReForge"},
                }
            ]

        ugc_body: dict[str, Any] = {
            "author": person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        url = f"{self.API_BASE}/ugcPosts"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=ugc_body)
            if r.status_code == 429:
                db.add(
                    ActivityLog(
                        user_id=sa.user_id,
                        content_item_id=pv.content_item_id,
                        action="linkedin_rate_limited",
                        details={"status": 429},
                    )
                )
                await db.commit()
                raise RuntimeError("LinkedIn rate limited; retry later.")

            if r.status_code >= 400:
                pv.status = "failed"
                pv.error_message = (r.text or "")[:500]
                db.add(pv)
                await db.commit()
                r.raise_for_status()

        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        post_id = str(data.get("id") or data.get("urn") or "")

        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {**(pv.metadata_json or {}), "linkedin_ugc_id": post_id}
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="linkedin_published",
                details={"platform_variant_id": str(pv.id), "ugc_id": post_id},
            )
        )
        await db.commit()
        return {"ok": True, "linkedin_ugc_id": post_id}

    async def _upload_media_asset(self, *, access_token: str, owner_urn: str, media_url: str) -> tuple[str, str]:
        """
        Upload media to LinkedIn and return (asset_urn, shareMediaCategory).

        Supports image + video via LinkedIn's registerUpload API.
        """
        # 1) Download media (LinkedIn requires uploading bytes to the provided uploadUrl)
        with tempfile.TemporaryDirectory(prefix="reforge_li_") as td:
            filename = os.path.join(td, "media")
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                r = await client.get(media_url)
                r.raise_for_status()
                # Try to keep extension for mime guessing
                guessed_ext = ""
                base = media_url.split("?", 1)[0].lower()
                for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".m4v"):
                    if base.endswith(ext):
                        guessed_ext = ext
                        break
                path = filename + guessed_ext
                with open(path, "wb") as f:
                    f.write(r.content)

            mime, _ = mimetypes.guess_type(path)
            mime = mime or "application/octet-stream"
            is_video = mime.startswith("video/")
            is_image = mime.startswith("image/")
            if not (is_video or is_image):
                raise ValueError(f"Unsupported LinkedIn media type: {mime}")

            # 2) registerUpload
            recipe = "urn:li:digitalmediaRecipe:feedshare-video" if is_video else "urn:li:digitalmediaRecipe:feedshare-image"
            register_body = {
                "registerUploadRequest": {
                    "recipes": [recipe],
                    "owner": owner_urn,
                    "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}],
                }
            }
            headers = {
                "authorization": f"Bearer {access_token}",
                "x-restli-protocol-version": "2.0.0",
                "content-type": "application/json",
            }
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                rr = await client.post(f"{self.API_BASE}/assets?action=registerUpload", headers=headers, json=register_body)
                rr.raise_for_status()
                reg = rr.json()

            value = (reg or {}).get("value") or {}
            asset = str(value.get("asset") or "")
            uploads = value.get("uploadMechanism") or {}
            http_req = uploads.get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest") or {}
            upload_url = str(http_req.get("uploadUrl") or "")
            if not asset or not upload_url:
                raise RuntimeError("LinkedIn registerUpload did not return asset or uploadUrl")

            # 3) Upload bytes
            data = open(path, "rb").read()
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                up = await client.put(upload_url, content=data, headers={"authorization": f"Bearer {access_token}", "content-type": mime})
                up.raise_for_status()

            # LinkedIn UGC expects categories IMAGE|VIDEO for shareMediaCategory
            return asset, ("VIDEO" if is_video else "IMAGE")

    async def _ensure_access_token(self, sa: SocialAccount, db: AsyncSession) -> str:
        access = decrypt_token(sa.access_token_encrypted)
        if not access:
            raise ValueError("Missing LinkedIn access token")

        now = datetime.now(timezone.utc)
        expires = sa.token_expires_at
        if expires is None:
            return access
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now + timedelta(minutes=2):
            return access

        refresh = decrypt_token(sa.refresh_token_encrypted)
        if not refresh:
            raise ValueError("LinkedIn token expired and no refresh token stored")

        cid = settings.LINKEDIN_CLIENT_ID
        csec = settings.LINKEDIN_CLIENT_SECRET
        if not cid or not csec:
            raise ValueError("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET for token refresh")

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": cid,
                    "client_secret": csec,
                },
            )
            r.raise_for_status()
            tok = r.json()

        new_access = str(tok.get("access_token") or "")
        new_refresh = tok.get("refresh_token")
        expires_in = int(tok.get("expires_in") or 5184000)

        sa.access_token_encrypted = encrypt_token(new_access)
        if new_refresh:
            sa.refresh_token_encrypted = encrypt_token(str(new_refresh))
        sa.token_expires_at = now + timedelta(seconds=expires_in)
        db.add(sa)
        await db.commit()
        return new_access

    def _person_urn(self, sa: SocialAccount) -> str | None:
        meta = sa.metadata_json or {}
        urn = meta.get("person_urn") or meta.get("linkedin_person_urn")
        if urn:
            return str(urn).strip()
        # Sometimes stored on platform_user_id as raw id
        pid = sa.platform_user_id
        if pid and not str(pid).startswith("urn:"):
            return f"urn:li:person:{pid}"
        return str(pid).strip() if pid else None

    async def _load_variant_and_account(self, variant_id: str, db: AsyncSession) -> tuple[PlatformVariant, SocialAccount]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != self.PLATFORM:
            raise ValueError("PlatformVariant is not LinkedIn")

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
            raise ValueError("No active LinkedIn SocialAccount for user")
        return pv, sa
