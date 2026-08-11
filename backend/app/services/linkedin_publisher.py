"""
Direct LinkedIn publisher (UGC posts via OAuth 2.0).

Mirrors `upload_video_to_youtube`:
    upload_post_to_linkedin(user_id, content_id) -> dict[str, Any]

Flow:
    Text-only:
        POST /v2/ugcPosts                  with `shareMediaCategory = NONE`
    Image:
        1. POST /v2/assets?action=registerUpload  -> uploadUrl + asset URN
        2. PUT image bytes to uploadUrl
        3. POST /v2/ugcPosts with shareMediaCategory = IMAGE + media[].media = asset URN
    Video:
        Same protocol as image but with `urn:li:digitalmediaRecipe:feedshare-video`
        and shareMediaCategory = VIDEO.

The author URN comes from the LinkedIn OAuth callback (we save the
`urn:li:person:<sub>` string into PlatformConnection.scopes alongside the
granted scopes).
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.services._publish_common import (
    compose_caption,
    is_fallback_text,
    load_connection,
    load_content,
    load_variant,
    mark_failed,
    mark_published,
    mark_publishing,
    variant_meta,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com"


async def upload_post_to_linkedin(user_id: str, content_id: str) -> dict[str, Any]:
    user_uuid = UUID(str(user_id))
    content_uuid = UUID(str(content_id))
    async with SessionLocal() as db:
        return await _publish(db, user_uuid, content_uuid)


async def _publish(db: AsyncSession, user_id: UUID, content_id: UUID) -> dict[str, Any]:
    conn = await load_connection(db, user_id=user_id, platform="linkedin")
    if conn is None or not conn.access_token:
        return {"ok": False, "error": "linkedin_not_connected"}

    person_urn = _extract_person_urn(conn.scopes)
    if not person_urn:
        return {"ok": False, "error": "linkedin_member_urn_missing"}

    item = await load_content(db, user_id=user_id, content_id=content_id)
    if item is None:
        return {"ok": False, "error": "content_not_found"}

    pv = await load_variant(db, content_item_id=item.id, platform="linkedin")
    if pv is None:
        return {"ok": False, "error": "linkedin_variant_missing"}

    text = compose_caption(pv.caption, pv.hashtags)
    if is_fallback_text(pv.caption, *(pv.hashtags or [])):
        return {
            "status": "blocked",
            "reason": "AI generation failed — please re-process content before publishing",
            "platform": "linkedin",
        }

    await mark_publishing(db, pv)

    media_url = (pv.media_url or item.original_file_url or "").strip()
    is_image = _looks_like_image(media_url) if media_url else False
    is_video = bool(media_url) and not is_image

    try:
        asset_urn: str | None = None
        if media_url:
            asset_urn = await _register_and_upload_asset(
                access_token=conn.access_token,
                person_urn=person_urn,
                media_url=media_url,
                is_video=is_video,
            )

        post_id = await _create_ugc_post(
            access_token=conn.access_token,
            person_urn=person_urn,
            text=text,
            asset_urn=asset_urn,
            is_video=is_video,
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        await mark_failed(
            db, pv,
            error=f"http_{exc.response.status_code}:{body[:200]}",
            user_id=user_id,
            log_action="linkedin_publish_failed",
            log_details={"status_code": exc.response.status_code, "body": body},
        )
        return {"ok": False, "error": "publish_failed", "status_code": exc.response.status_code}
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"{type(exc).__name__}: {exc}",
            user_id=user_id,
            log_action="linkedin_publish_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "publish_failed"}

    await mark_published(
        db, pv,
        extra_metadata={"linkedin_post_id": post_id, "author_urn": person_urn},
        user_id=user_id,
        log_action="linkedin_published",
        log_details={"linkedin_post_id": post_id},
    )
    return {"ok": True, "linkedin_post_id": post_id}


def _extract_person_urn(scopes_blob: str | None) -> str | None:
    """We stored `urn=urn:li:person:<sub>` in the scopes column on callback."""
    if not scopes_blob:
        return None
    for tok in scopes_blob.split():
        if tok.startswith("urn=urn:li:"):
            return tok[len("urn="):]
    return None


def _looks_like_image(media_url: str) -> bool:
    base = media_url.lower().split("?", 1)[0]
    return base.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


async def _register_and_upload_asset(
    *,
    access_token: str,
    person_urn: str,
    media_url: str,
    is_video: bool,
) -> str:
    recipe = (
        "urn:li:digitalmediaRecipe:feedshare-video"
        if is_video
        else "urn:li:digitalmediaRecipe:feedshare-image"
    )
    payload = {
        "registerUploadRequest": {
            "recipes": [recipe],
            "owner": person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        reg = await client.post(
            f"{API_BASE}/v2/assets?action=registerUpload",
            headers={
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "x-restli-protocol-version": "2.0.0",
            },
            json=payload,
        )
        reg.raise_for_status()
        data = reg.json() or {}
        value = data.get("value") or {}
        asset_urn = str(value.get("asset") or "")
        upload_mech = (
            (value.get("uploadMechanism") or {})
            .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest")
            or {}
        )
        upload_url = upload_mech.get("uploadUrl")
        if not asset_urn or not upload_url:
            raise RuntimeError("linkedin_register_upload_missing_fields")

        # Download the media bytes once, then PUT to LinkedIn's signed upload URL.
        with tempfile.TemporaryDirectory(prefix="reforge_li_media_") as td:
            path = os.path.join(td, "media.bin")
            r = await client.get(media_url)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)

            with open(path, "rb") as f:
                up = await client.put(
                    upload_url,
                    headers={"authorization": f"Bearer {access_token}"},
                    content=f.read(),
                )
                if up.status_code >= 400:
                    raise httpx.HTTPStatusError("upload failed", request=up.request, response=up)

    return asset_urn


async def _create_ugc_post(
    *,
    access_token: str,
    person_urn: str,
    text: str,
    asset_urn: str | None,
    is_video: bool,
) -> str:
    media_category = "NONE"
    media_block: list[dict[str, Any]] = []
    if asset_urn:
        media_category = "VIDEO" if is_video else "IMAGE"
        media_block = [
            {
                "status": "READY",
                "media": asset_urn,
            }
        ]

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": media_category,
                "media": media_block,
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{API_BASE}/v2/ugcPosts",
            headers={
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "x-restli-protocol-version": "2.0.0",
            },
            json=payload,
        )
        r.raise_for_status()
        # Response includes `x-restli-id` header containing the URN, body has `id`.
        data: dict[str, Any] = {}
        try:
            data = r.json()
        except Exception:
            data = {}
    return str(data.get("id") or r.headers.get("x-restli-id") or "").strip()
