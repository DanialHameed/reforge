"""
Direct Instagram publisher (Business / Creator accounts via Meta Graph API).

Mirrors `upload_video_to_youtube`:
    upload_post_to_instagram(user_id, content_id) -> dict[str, Any]

Flow (Graph API "Content Publishing"):
    1. Create a media container at /{ig_user_id}/media
    2. Poll until status_code == FINISHED
    3. Publish container at /{ig_user_id}/media_publish

Requires the user to have:
    - A connected Facebook Page
    - An Instagram Business / Creator account linked to that Page
    - The Meta consent granted at /api/v1/connections/meta/callback
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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


def _graph_base() -> str:
    return f"https://graph.facebook.com/{settings.META_GRAPH_VERSION or 'v19.0'}"


async def upload_post_to_instagram(user_id: str, content_id: str) -> dict[str, Any]:
    user_uuid = UUID(str(user_id))
    content_uuid = UUID(str(content_id))
    async with SessionLocal() as db:
        return await _publish(db, user_uuid, content_uuid)


async def _publish(db: AsyncSession, user_id: UUID, content_id: UUID) -> dict[str, Any]:
    conn = await load_connection(db, user_id=user_id, platform="instagram")
    if conn is None or not conn.access_token:
        return {"ok": False, "error": "instagram_not_connected"}

    item = await load_content(db, user_id=user_id, content_id=content_id)
    if item is None:
        return {"ok": False, "error": "content_not_found"}

    pv = await load_variant(db, content_item_id=item.id, platform="instagram")
    if pv is None:
        return {"ok": False, "error": "instagram_variant_missing"}

    media_url = (pv.media_url or item.original_file_url or "").strip()
    if not media_url:
        return {"ok": False, "error": "missing_media_url"}

    caption = compose_caption(pv.caption, pv.hashtags, max_tags=30)
    if is_fallback_text(pv.caption, *(pv.hashtags or [])):
        return {
            "status": "blocked",
            "reason": "AI generation failed — please re-process content before publishing",
            "platform": "instagram",
        }

    await mark_publishing(db, pv)

    try:
        ig_user_id, page_token = await _resolve_ig_user(conn.access_token)
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"ig_account_lookup_failed:{type(exc).__name__}",
            user_id=user_id,
            log_action="instagram_account_lookup_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "ig_account_lookup_failed"}

    meta = variant_meta(pv)
    is_image = _looks_like_image(media_url)
    is_reel = bool(meta.get("is_reel") or meta.get("variant") == "reel")

    try:
        creation_id = await _create_container(
            ig_user_id=ig_user_id,
            access_token=page_token,
            media_url=media_url,
            caption=caption,
            is_image=is_image,
            is_reel=is_reel,
        )
        await _wait_until_ready(creation_id, page_token)
        media_id = await _publish_container(ig_user_id, creation_id, page_token)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        await mark_failed(
            db, pv,
            error=f"http_{exc.response.status_code}:{body[:200]}",
            user_id=user_id,
            log_action="instagram_publish_failed",
            log_details={"status_code": exc.response.status_code, "body": body},
        )
        return {"ok": False, "error": "publish_failed", "status_code": exc.response.status_code}
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"{type(exc).__name__}: {exc}",
            user_id=user_id,
            log_action="instagram_publish_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "publish_failed"}

    kind = "reel" if (is_reel and not is_image) else ("image" if is_image else "video")
    await mark_published(
        db, pv,
        extra_metadata={"instagram_media_id": media_id, "instagram_kind": kind, "ig_user_id": ig_user_id},
        user_id=user_id,
        log_action="instagram_published",
        log_details={"instagram_media_id": media_id, "kind": kind},
    )
    return {"ok": True, "instagram_media_id": media_id, "kind": kind}


def _looks_like_image(media_url: str) -> bool:
    base = media_url.lower().split("?", 1)[0]
    return base.endswith((".jpg", ".jpeg", ".png", ".webp"))


async def _resolve_ig_user(user_access_token: str) -> tuple[str, str]:
    """Return (ig_user_id, page_access_token) using the user's first Page."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(
            f"{_graph_base()}/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account",
                "access_token": user_access_token,
            },
        )
        r.raise_for_status()
        data = r.json() or {}
    for page in data.get("data") or []:
        ig = page.get("instagram_business_account") or {}
        ig_id = str(ig.get("id") or "")
        page_token = str(page.get("access_token") or "")
        if ig_id and page_token:
            return ig_id, page_token
    raise ValueError("no_instagram_business_account_linked")


async def _create_container(
    *,
    ig_user_id: str,
    access_token: str,
    media_url: str,
    caption: str,
    is_image: bool,
    is_reel: bool,
) -> str:
    url = f"{_graph_base()}/{ig_user_id}/media"
    params: dict[str, Any] = {"caption": caption, "access_token": access_token}
    if is_image:
        params["image_url"] = media_url
    elif is_reel:
        params["media_type"] = "REELS"
        params["video_url"] = media_url
    else:
        params["media_type"] = "VIDEO"
        params["video_url"] = media_url

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, data=params)
        r.raise_for_status()
        data = r.json() or {}
    cid = str(data.get("id") or "").strip()
    if not cid:
        raise RuntimeError("ig_container_missing_id")
    return cid


async def _wait_until_ready(creation_id: str, access_token: str, *, max_wait_seconds: int = 240) -> None:
    """Poll the container's status_code until FINISHED (or fail/timeout)."""
    delay = 3
    waited = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        while waited < max_wait_seconds:
            r = await client.get(
                f"{_graph_base()}/{creation_id}",
                params={"fields": "status_code,status", "access_token": access_token},
            )
            if r.status_code == 200:
                data = r.json() or {}
                code = str(data.get("status_code") or "").upper()
                if code == "FINISHED":
                    return
                if code in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"ig_container_status:{code}")
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay + 2, 15)
    raise RuntimeError("ig_container_timeout")


async def _publish_container(ig_user_id: str, creation_id: str, access_token: str) -> str:
    url = f"{_graph_base()}/{ig_user_id}/media_publish"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, data={"creation_id": creation_id, "access_token": access_token})
        r.raise_for_status()
        data = r.json() or {}
    media_id = str(data.get("id") or "").strip()
    if not media_id:
        raise RuntimeError("ig_publish_missing_id")
    return media_id
