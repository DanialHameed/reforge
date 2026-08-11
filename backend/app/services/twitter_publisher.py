"""
Direct Twitter / X publisher.

Mirrors `app/services/youtube_publisher.upload_video_to_youtube`:
    upload_post_to_twitter(user_id, content_id) -> dict[str, Any]

- Reads OAuth2 tokens from `PlatformConnection` (rows created by the
  /api/v1/connections/twitter/* OAuth flow).
- Refreshes the access token via the OAuth2 refresh_token grant when
  `expires_at` is in the past (or close to it).
- Uploads media (image or video) via the v1.1 chunked endpoint and posts
  the tweet via v2 `/2/tweets`.
- Supports threads when the YouTube/Twitter variant carries
  `metadata_json.thread_tweets`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.connection import PlatformConnection
from app.services._publish_common import (
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

API_BASE = "https://api.twitter.com"
UPLOAD_BASE = "https://upload.twitter.com"
TWEET_CHAR_LIMIT = 280


async def upload_post_to_twitter(user_id: str, content_id: str) -> dict[str, Any]:
    user_uuid = UUID(str(user_id))
    content_uuid = UUID(str(content_id))
    async with SessionLocal() as db:
        return await _publish(db, user_uuid, content_uuid)


async def _publish(db: AsyncSession, user_id: UUID, content_id: UUID) -> dict[str, Any]:
    conn = await load_connection(db, user_id=user_id, platform="twitter")
    if conn is None or not conn.access_token:
        return {"ok": False, "error": "twitter_not_connected"}

    item = await load_content(db, user_id=user_id, content_id=content_id)
    if item is None:
        return {"ok": False, "error": "content_not_found"}

    pv = await load_variant(db, content_item_id=item.id, platform="twitter")
    if pv is None:
        return {"ok": False, "error": "twitter_variant_missing"}

    meta = variant_meta(pv)
    text = (pv.caption or "").strip()
    thread = meta.get("thread_tweets") or meta.get("thread") or []
    if not isinstance(thread, list):
        thread = []

    if is_fallback_text(text, *(str(t) for t in thread)):
        logger.warning("twitter.blocked_fallback_publish", extra={"content_id": str(content_id)})
        return {
            "status": "blocked",
            "reason": "AI generation failed — please re-process content before publishing",
            "platform": "twitter",
        }

    await mark_publishing(db, pv)

    try:
        access_token = await _ensure_access_token(db, conn)
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"token_refresh_failed:{type(exc).__name__}",
            user_id=user_id,
            log_action="twitter_token_refresh_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "token_refresh_failed"}

    media_id: str | None = None
    if pv.media_url:
        try:
            media_id = await _upload_media(pv.media_url, access_token)
        except Exception as exc:
            logger.warning("twitter.media_upload_failed", extra={"error": str(exc)})
            media_id = None

    try:
        if thread:
            tweet_ids = await _post_thread(thread, access_token, first_media_id=media_id)
            await mark_published(
                db, pv,
                extra_metadata={"tweet_ids": tweet_ids, "is_thread": True},
                user_id=user_id,
                log_action="twitter_published",
                log_details={"tweet_ids": tweet_ids, "is_thread": True},
            )
            return {"ok": True, "tweet_ids": tweet_ids}

        tweet_id = await _post_single_tweet(text[:TWEET_CHAR_LIMIT], access_token, media_id=media_id)
        await mark_published(
            db, pv,
            extra_metadata={"tweet_id": tweet_id},
            user_id=user_id,
            log_action="twitter_published",
            log_details={"tweet_id": tweet_id},
        )
        return {"ok": True, "tweet_id": tweet_id}

    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        await mark_failed(
            db, pv,
            error=f"http_{exc.response.status_code}:{body[:200]}",
            user_id=user_id,
            log_action="twitter_publish_failed",
            log_details={"status_code": exc.response.status_code, "body": body},
        )
        return {"ok": False, "error": "tweet_failed", "status_code": exc.response.status_code}
    except Exception as exc:
        await mark_failed(
            db, pv,
            error=f"{type(exc).__name__}: {exc}",
            user_id=user_id,
            log_action="twitter_publish_failed",
            log_details={"error": str(exc)[:300]},
        )
        return {"ok": False, "error": "tweet_failed"}


async def _ensure_access_token(db: AsyncSession, conn: PlatformConnection) -> str:
    """Return a fresh access token, refreshing if expired."""
    access = (conn.access_token or "").strip()
    if not access:
        raise RuntimeError("missing_access_token")

    needs_refresh = False
    expires = conn.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires:
        if expires <= datetime.now(timezone.utc) + timedelta(seconds=60):
            needs_refresh = True
    if not needs_refresh:
        return access

    refresh = (conn.refresh_token or "").strip()
    if not refresh:
        return access  # try with whatever we have

    cid = (settings.TWITTER_CLIENT_ID or "").strip()
    csec = (settings.TWITTER_CLIENT_SECRET or "").strip()
    if not cid:
        raise RuntimeError("twitter_client_id_missing")

    headers = {"content-type": "application/x-www-form-urlencoded"}
    if csec:
        headers["authorization"] = "Basic " + base64.b64encode(
            f"{cid}:{csec}".encode("utf-8")
        ).decode("ascii")

    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(f"{API_BASE}/2/oauth2/token", data=data, headers=headers)
        r.raise_for_status()
        tok = r.json()

    access = str(tok.get("access_token") or "").strip() or access
    new_refresh = str(tok.get("refresh_token") or "").strip()
    expires_in = int(tok.get("expires_in") or 0)

    conn.access_token = access
    if new_refresh:
        conn.refresh_token = new_refresh
    if expires_in:
        conn.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    db.add(conn)
    await db.commit()
    return access


async def _upload_media(media_url: str, access_token: str) -> str:
    with tempfile.TemporaryDirectory(prefix="reforge_tw_media_") as td:
        path = os.path.join(td, "media.bin")
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            r = await client.get(media_url)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)

        size = os.path.getsize(path)
        is_video = (
            media_url.lower().split("?", 1)[0].endswith((".mp4", ".mov", ".webm"))
            or size > 5 * 1024 * 1024
        )
        media_type = "video/mp4" if is_video else "image/jpeg"
        headers = {"authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            init = await client.post(
                f"{UPLOAD_BASE}/1.1/media/upload.json",
                headers=headers,
                data={
                    "command": "INIT",
                    "total_bytes": str(size),
                    "media_type": media_type,
                    "media_category": "tweet_video" if is_video else "tweet_image",
                },
            )
            init.raise_for_status()
            media_id = str(init.json().get("media_id_string") or "").strip()
            if not media_id:
                raise RuntimeError("twitter_media_init_missing_id")

            chunk_size = 5 * 1024 * 1024
            seg = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    append = await client.post(
                        f"{UPLOAD_BASE}/1.1/media/upload.json",
                        headers=headers,
                        data={
                            "command": "APPEND",
                            "media_id": media_id,
                            "segment_index": str(seg),
                        },
                        files={"media": chunk},
                    )
                    append.raise_for_status()
                    seg += 1

            fin = await client.post(
                f"{UPLOAD_BASE}/1.1/media/upload.json",
                headers=headers,
                data={"command": "FINALIZE", "media_id": media_id},
            )
            fin.raise_for_status()

            # Wait for STATUS=succeeded if the FINALIZE response asks us to (videos).
            data = fin.json()
            if isinstance(data, dict) and data.get("processing_info"):
                await _wait_for_processing(client, headers, media_id)

        return media_id


async def _wait_for_processing(client: httpx.AsyncClient, headers: dict, media_id: str) -> None:
    for _ in range(20):
        r = await client.get(
            f"{UPLOAD_BASE}/1.1/media/upload.json",
            headers=headers,
            params={"command": "STATUS", "media_id": media_id},
        )
        if r.status_code != 200:
            return
        info = (r.json() or {}).get("processing_info") or {}
        state = str(info.get("state") or "").lower()
        if state in ("succeeded", "failed"):
            return
        await asyncio.sleep(min(int(info.get("check_after_secs") or 2), 10))


async def _post_single_tweet(
    text: str,
    access_token: str,
    *,
    media_id: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    payload: dict[str, Any] = {"text": text}
    if media_id:
        payload["media"] = {"media_ids": [media_id]}
    if in_reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": in_reply_to}

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{API_BASE}/2/tweets",
            headers={
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json() or {}
    tweet_id = str((data.get("data") or {}).get("id") or "").strip()
    if not tweet_id:
        raise RuntimeError("twitter_tweet_missing_id")
    return tweet_id


async def _post_thread(
    thread: list[Any],
    access_token: str,
    *,
    first_media_id: str | None = None,
) -> list[str]:
    ids: list[str] = []
    previous: str | None = None
    for i, t in enumerate(thread):
        text = str(t).strip()[:TWEET_CHAR_LIMIT]
        if not text:
            continue
        media_id = first_media_id if i == 0 else None
        tid = await _post_single_tweet(text, access_token, media_id=media_id, in_reply_to=previous)
        ids.append(tid)
        previous = tid
    return ids
