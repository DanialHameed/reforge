from __future__ import annotations

import gc
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from moviepy.editor import AudioClip, ImageClip
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.connection import PlatformConnection
from app.models.content_orm import ContentItem, PlatformVariant

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# Broader scope (superset of `youtube.readonly`); needed for `videos().update` to
# re-apply privacy/embeddable after upload. Kept as `YOUTUBE_READONLY_SCOPE` for
# backward-compatible imports.
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _windows_safe_cleanup(path: str) -> None:
    """Retry-based cleanup that handles Windows file handle delays."""
    gc.collect()  # Force garbage collection to release file handles
    for attempt in range(6):
        try:
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=False)
            return
        except (PermissionError, OSError):
            if attempt < 5:
                time.sleep(0.3 * (attempt + 1))
            else:
                shutil.rmtree(path, ignore_errors=True)
                logger.warning("youtube.temp_cleanup_partial", extra={"path": path})


async def upload_video_to_youtube(user_id: str, content_id: str) -> dict[str, Any]:
    """
    Upload a content item's media to YouTube as private.

    - Uses stored refresh_token from PlatformConnection(platform='youtube')
    - Uses generated title/description from the YouTube PlatformVariant
    - Downloads the media URL to a temp file and uploads via YouTube Data API
    """
    user_uuid = UUID(str(user_id))
    content_uuid = UUID(str(content_id))

    async with SessionLocal() as db:
        return await _upload_video_to_youtube_db(db, user_uuid, content_uuid)


async def _upload_video_to_youtube_db(db: AsyncSession, user_id: UUID, content_id: UUID) -> dict[str, Any]:
    conn = (
        await db.execute(
            select(PlatformConnection).where(
                PlatformConnection.user_id == user_id,
                PlatformConnection.platform == "youtube",
            )
        )
    ).scalar_one_or_none()
    if conn is None or not conn.refresh_token:
        return {"ok": False, "error": "youtube_not_connected"}

    item = (
        await db.execute(
            select(ContentItem).where(ContentItem.id == content_id, ContentItem.user_id == user_id)
        )
    ).scalar_one_or_none()
    if item is None:
        return {"ok": False, "error": "content_not_found"}
    if not item.original_file_url:
        return {"ok": False, "error": "missing_media_url"}

    pv = (
        await db.execute(
            select(PlatformVariant).where(
                PlatformVariant.content_item_id == item.id,
                PlatformVariant.platform == "youtube",
            )
        )
    ).scalar_one_or_none()
    if pv is None:
        return {"ok": False, "error": "youtube_variant_missing"}

    title = (pv.caption or "").strip() or (item.title or "ReForge Upload")
    description = ""
    tags: list[str] = []
    if isinstance(pv.metadata_json, dict):
        description = str(pv.metadata_json.get("description") or "").strip()
        raw_tags = pv.metadata_json.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(x) for x in raw_tags if str(x).strip()]

    youtube_data = {"title": title, "description": description}

    # Safety guard — never publish generic fallback content to YouTube
    FALLBACK_TITLE_SIGNALS = [
        "amazing content you need to see",
        "incredible content created with reforge",
        "like and subscribe for more",
    ]

    title_lower = (youtube_data.get("title", "")).lower()
    description_lower = (youtube_data.get("description", "")).lower()

    is_fallback_content = any(
        sig in title_lower or sig in description_lower for sig in FALLBACK_TITLE_SIGNALS
    )

    if is_fallback_content:
        logger.warning(
            "youtube.blocked_fallback_publish",
            extra={"content_id": str(content_id), "reason": "detected_generic_fallback_content"},
        )
        return {
            "status": "blocked",
            "reason": "AI generation failed — please re-process content before publishing",
            "platform": "youtube",
        }

    # Mark as publishing (best-effort)
    pv.status = "publishing"
    db.add(pv)
    await db.commit()

    creds = _build_youtube_credentials(refresh_token=str(conn.refresh_token))
    try:
        # Refresh access token
        creds.refresh(Request())
    except Exception:
        pv.status = "failed"
        pv.error_message = "token_refresh_failed"
        db.add(pv)
        await db.commit()
        return {"ok": False, "error": "token_refresh_failed"}

    temp_dir = tempfile.mkdtemp(prefix="reforge_yt_")
    resp: dict[str, Any] | None = None
    try:
        temp_video_path = os.path.join(temp_dir, "upload_video.mp4")
        image_path = os.path.join(temp_dir, "upload_image")
        media_url = (pv.media_url or item.original_file_url or "").strip()
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(media_url)
            r.raise_for_status()
            content_type = str(r.headers.get("content-type") or "").lower()
            blob = bytes(r.content)

        is_image = False
        # Prefer the actual media URL / response content-type for detection.
        if content_type.startswith("image/"):
            is_image = True
        if media_url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            is_image = True

        if is_image:
            # Create a 5-second static video from the image, with a 5-second silent audio track.
            # YouTube often rejects video-only uploads without an audio stream.
            ext = ".jpg"
            orig = (item.original_file_url or "").lower()
            if "png" in content_type or orig.endswith(".png"):
                ext = ".png"
            image_path = image_path + ext
            with open(image_path, "wb") as f:
                f.write(blob)

            clip = None
            audio = None
            try:
                clip = ImageClip(image_path, duration=5.0).set_fps(30)
                audio = AudioClip(lambda t: 0.0, duration=5.0, fps=44100)
                clip = clip.set_audio(audio)
                clip.write_videofile(
                    temp_video_path,
                    codec="libx264",
                    audio_codec="aac",
                    fps=30,
                    preset="medium",
                    threads=1,
                    logger=None,
                )
            finally:
                # Windows can hold file handles open unless we explicitly close MoviePy objects.
                try:
                    if clip is not None:
                        clip.close()
                except Exception:
                    pass
                try:
                    if audio is not None:
                        audio.close()
                except Exception:
                    pass
        else:
            with open(temp_video_path, "wb") as f:
                f.write(blob)

        # Disable discovery caching to avoid noisy warnings and extra file I/O.
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags[:30],
                "categoryId": "22",  # People & Blogs (safe default)
            },
            "status": {"privacyStatus": "private"},
        }

        try:
            # IMPORTANT (Windows): open the file ourselves and close it before temp cleanup.
            with open(temp_video_path, "rb") as fh:
                media = MediaIoBaseUpload(fh, mimetype="video/mp4", resumable=True)
                resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        except Exception as exc:
            pv.status = "failed"
            pv.error_message = f"upload_failed:{type(exc).__name__}"
            db.add(pv)
            await db.commit()
            return {"ok": False, "error": "upload_failed"}
    finally:
        _windows_safe_cleanup(temp_dir)

    video_id = str((resp or {}).get("id") or "").strip()
    if not video_id:
        pv.status = "failed"
        pv.error_message = "missing_video_id"
        db.add(pv)
        await db.commit()
        return {"ok": False, "error": "missing_video_id"}

    pv.status = "published"
    pv.published_at = datetime.now(timezone.utc)
    pv.error_message = None
    pv.metadata_json = {**(pv.metadata_json or {}), "youtube_video_id": video_id, "privacy": "private"}
    db.add(pv)
    await db.commit()
    return {"ok": True, "youtube_video_id": video_id}


def _build_youtube_credentials(*, refresh_token: str) -> Credentials:
    client_id = (settings.GOOGLE_CLIENT_ID or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (settings.GOOGLE_CLIENT_SECRET or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google OAuth is not configured (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET).")
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READONLY_SCOPE],
    )
