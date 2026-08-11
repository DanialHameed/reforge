"""
Direct Facebook Pages publisher.

Mirrors `upload_video_to_youtube`:
    upload_post_to_facebook(user_id, content_id) -> dict[str, Any]

- Reads OAuth tokens from `PlatformConnection` (rows created by the Meta
  consent flow at /api/v1/connections/meta/callback).
- Resolves a Page id + Page access token from the user token via /me/accounts.
- Routes by media type: image -> /{page}/photos, video -> /{page}/videos,
  reel-style 9:16 video (when metadata_json.is_reel is set) -> /{page}/video_reels.
"""

from __future__ import annotations

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


async def upload_post_to_facebook(user_id: str, content_id: str) -> dict[str, Any]:
    user_uuid = UUID(str(user_id))
    content_uuid = UUID(str(content_id))
    async with SessionLocal() as db:
        return await _publish(db, user_uuid, content_uuid)


async def _publish(db: AsyncSession, user_id: UUID, content_id: UUID) -> dict[str, Any]:
    conn = await load_connection(db, user_id=user_id, platform="facebook")
    if conn is None or not conn.access_token:
        return {"ok": False, "error": "facebook_not_connected"}

    item = await load_content(db, user_id=user_id, content_id=content_id)
    if item is None:
        return {"ok": False, "error": "content_not_found"}

    pv = await load_variant(db, content_item_id=item.id, platform="facebook")
    if pv is None:
        return {"ok": False, "error": "facebook_variant_missing"}

    media_url = (pv.media_url or item.original_file_url or "").strip()
    caption = compose_caption(pv.caption, pv.hashtags)

    if is_fallback_text(pv.caption, *(pv.hashtags or [])):
        return {
            "status": "blocked",
            "reason": "AI generation failed — please re-process content before publishing",
            "platform": "facebook",
        }

    await mark_publishing(db, pv)

    try:
        page_id, page_name, page_token = await _resolve_page(conn.access_token)
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"page_resolution_failed:{type(exc).__name__}",
            user_id=user_id,
            log_action="facebook_page_lookup_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "page_lookup_failed"}

    meta = variant_meta(pv)
    is_reel = bool(meta.get("is_reel") or meta.get("variant") == "reel")
    is_image = _looks_like_image(media_url)

    try:
        if not media_url:
            external_id = await _publish_text_post(page_id, page_token, caption)
            kind = "post"
        elif is_image:
            external_id = await _publish_photo(page_id, page_token, media_url, caption)
            kind = "photo"
        elif is_reel:
            external_id = await _publish_reel(page_id, page_token, media_url, caption)
            kind = "reel"
        else:
            external_id = await _publish_video(page_id, page_token, media_url, caption, title=pv.caption)
            kind = "video"
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        await mark_failed(
            db, pv,
            error=f"http_{exc.response.status_code}:{body[:200]}",
            user_id=user_id,
            log_action="facebook_publish_failed",
            log_details={"status_code": exc.response.status_code, "body": body},
        )
        return {"ok": False, "error": "publish_failed", "status_code": exc.response.status_code}
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"{type(exc).__name__}: {exc}",
            user_id=user_id,
            log_action="facebook_publish_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "publish_failed"}

    await mark_published(
        db, pv,
        extra_metadata={
            "facebook_id": external_id,
            "facebook_kind": kind,
            "page_id": page_id,
            "page_name": page_name,
        },
        user_id=user_id,
        log_action="facebook_published",
        log_details={"facebook_id": external_id, "kind": kind, "page_id": page_id},
    )
    return {"ok": True, "facebook_id": external_id, "kind": kind, "page_name": page_name}


def _looks_like_image(media_url: str) -> bool:
    base = media_url.lower().split("?", 1)[0]
    return base.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


async def _resolve_page(user_access_token: str) -> tuple[str, str, str]:
    url = f"{_graph_base()}/me/accounts"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, params={"access_token": user_access_token})
        r.raise_for_status()
        data = r.json()
    pages = data.get("data") or []
    if not pages:
        raise ValueError("no_facebook_pages")
    p = pages[0]
    page_id = str(p.get("id") or "")
    page_name = str(p.get("name") or "")
    page_token = str(p.get("access_token") or "")
    if not page_id or not page_token:
        raise ValueError("invalid_page_response")
    return page_id, page_name, page_token


async def _publish_photo(page_id: str, page_token: str, media_url: str, caption: str) -> str:
    url = f"{_graph_base()}/{page_id}/photos"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            url,
            data={"url": media_url, "caption": caption, "access_token": page_token},
        )
        r.raise_for_status()
        data = r.json() or {}
    return str(data.get("id") or data.get("post_id") or "").strip()


async def _publish_video(
    page_id: str, page_token: str, media_url: str, description: str, *, title: str | None
) -> str:
    url = f"{_graph_base()}/{page_id}/videos"
    payload = {
        "file_url": media_url,
        "description": description,
        "access_token": page_token,
    }
    if title:
        payload["title"] = title.strip()[:255]
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(url, data=payload)
        r.raise_for_status()
        data = r.json() or {}
    return str(data.get("id") or data.get("video_id") or "").strip()


async def _publish_reel(page_id: str, page_token: str, media_url: str, description: str) -> str:
    """
    Facebook Reels upload requires a 3-step protocol (start -> upload -> finish).
    For URL-hosted media, we use the simpler `video_reels` endpoint with `video_url`.
    """
    url = f"{_graph_base()}/{page_id}/video_reels"
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            url,
            data={
                "video_url": media_url,
                "description": description,
                "access_token": page_token,
                "upload_phase": "start",
            },
        )
        r.raise_for_status()
        data = r.json() or {}
    return str(data.get("video_id") or data.get("id") or "").strip()


async def _publish_text_post(page_id: str, page_token: str, message: str) -> str:
    url = f"{_graph_base()}/{page_id}/feed"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, data={"message": message, "access_token": page_token})
        r.raise_for_status()
        data = r.json() or {}
    return str(data.get("id") or data.get("post_id") or "").strip()
