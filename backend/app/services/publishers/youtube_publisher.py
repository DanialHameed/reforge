from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity_orm import ActivityLog
from app.models.content_orm import PlatformVariant
from app.models.social_orm import SocialAccount
from app.services.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

# After the resumable upload finishes, YouTube may still transcode. Wait until the video is
# playable or definitively failed before marking the variant published.
_YT_POST_UPLOAD_POLL_MAX_SEC = 600.0
_YT_POST_UPLOAD_POLL_FIRST_INTERVAL = 2.0
_YT_POST_UPLOAD_POLL_MAX_INTERVAL = 20.0

# Must match YouTube OAuth in `app.api.v1.connections`. The broader `youtube` scope
# is needed for `videos().update` (privacy / embeddable re-apply) and is a superset
# of `youtube.readonly`, so the videos.list/channels.list calls in poll/finalize still work.
_YOUTUBE_API_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# Allowed values for ``status.privacyStatus`` per YouTube Data API v3.
# Anything outside this set silently fell back to "public" historically (B-11).
_YOUTUBE_ALLOWED_PRIVACY: frozenset[str] = frozenset({"public", "unlisted", "private"})
_YOUTUBE_DEFAULT_PRIVACY = "public"


def _resolve_youtube_privacy(pv: PlatformVariant | None) -> str:
    """
    Resolve the requested YouTube privacy status from PlatformVariant metadata.

    Accepts ``privacy_status`` or ``youtube_privacy_status`` (synonym) on
    ``metadata_json``. Unknown / missing values default to ``"public"`` to
    preserve the historical behavior — earlier versions hard-coded
    ``"public"`` in two places (B-11), so callers that never set the field
    must continue to publish publicly.
    """
    meta = pv.metadata_json if (pv is not None and isinstance(pv.metadata_json, dict)) else {}
    raw = meta.get("privacy_status") or meta.get("youtube_privacy_status") or _YOUTUBE_DEFAULT_PRIVACY
    candidate = str(raw).strip().lower()
    return candidate if candidate in _YOUTUBE_ALLOWED_PRIVACY else _YOUTUBE_DEFAULT_PRIVACY


class InsufficientYouTubeReadScope(Exception):
    """Raised when the stored token cannot call videos.list / channels.list (missing readonly scope)."""


def _youtube_http_is_insufficient_scope(err: HttpError) -> bool:
    if getattr(err.resp, "status", None) != 403:
        return False
    try:
        raw = err.content if isinstance(err.content, bytes) else bytes(str(err.content or ""), "utf-8", errors="replace")
        text = raw.decode("utf-8", errors="replace").lower()
    except Exception:
        text = str(err).lower()
    return "insufficient" in text and "scope" in text


def _close_media_file_upload(media: MediaFileUpload | None) -> None:
    """Release file handle googleapiclient keeps open — required for Windows temp dir cleanup."""
    if media is None:
        return
    fd = getattr(media, "_fd", None)
    if fd is None:
        return
    try:
        fd.close()
    except Exception:
        pass
    try:
        setattr(media, "_fd", None)
    except Exception:
        pass


def _youtube_upload_suffix_and_mime(media_url: str, content_type_header: str | None) -> tuple[str, str]:
    """
    Temp upload filename + MIME for YouTube. JPEG/PNG mislabeled as video/mp4 yields
    uploads that stall or show 'Processing abandoned' in Studio.
    """
    ct = (content_type_header or "").split(";")[0].strip().lower()
    path_part = urlparse(media_url).path.lower()

    ext_mime: list[tuple[str, str, str]] = [
        (".mp4", "video/mp4", ".mp4"),
        (".m4v", "video/x-m4v", ".m4v"),
        (".webm", "video/webm", ".webm"),
        (".mov", "video/quicktime", ".mov"),
        (".mkv", "video/x-matroska", ".mkv"),
        (".avi", "video/x-msvideo", ".avi"),
        (".mpeg", "video/mpeg", ".mpeg"),
        (".mpg", "video/mpeg", ".mpg"),
    ]
    for suffix, mime, needle in ext_mime:
        if path_part.endswith(needle):
            return suffix, mime

    mime_to_suffix: dict[str, tuple[str, str]] = {
        "video/mp4": (".mp4", "video/mp4"),
        "video/webm": (".webm", "video/webm"),
        "video/quicktime": (".mov", "video/quicktime"),
        "video/x-matroska": (".mkv", "video/x-matroska"),
        "video/x-msvideo": (".avi", "video/x-msvideo"),
        "video/mpeg": (".mpeg", "video/mpeg"),
    }
    if ct in mime_to_suffix:
        return mime_to_suffix[ct]
    if ct.startswith("video/"):
        return ".mp4", ct
    return ".mp4", "video/mp4"


def _youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _sync_fetch_video_resource(yt: Any, video_id: str) -> dict[str, Any] | None:
    try:
        resp = (
            yt.videos()
            .list(part="status,snippet,processingDetails", id=video_id, maxResults=1)
            .execute()
        )
    except HttpError as e:
        if _youtube_http_is_insufficient_scope(e):
            raise InsufficientYouTubeReadScope(
                "YouTube token is missing required read scope; reconnect YouTube in Connections "
                "to grant the `youtube` scope."
            ) from e
        raise
    items = resp.get("items") or []
    return items[0] if items else None


def _format_youtube_failure(resource: dict[str, Any]) -> str:
    st = resource.get("status") or {}
    parts = [st.get("failureReason"), st.get("rejectionReason")]
    pd = resource.get("processingDetails") or {}
    parts.append(pd.get("processingFailureReason"))
    return " | ".join(str(p) for p in parts if p) or "unknown"


def _sync_ensure_visibility_and_embeddable(yt: Any, video_id: str, privacy_status: str) -> None:
    """
    Re-apply requested privacy + embeddable flags after upload.

    Some accounts or edge cases can leave uploads in a state that doesn't
    match the insert() snippet (e.g. policy review forces ``private``); a
    follow-up ``videos().update`` realigns visibility for most
    "published in app but invisible on channel" reports.

    Note: ``publicStatsViewable`` is meaningful only for ``public`` videos —
    YouTube ignores it for private/unlisted videos but accepts it silently.
    """
    if privacy_status not in _YOUTUBE_ALLOWED_PRIVACY:
        privacy_status = _YOUTUBE_DEFAULT_PRIVACY
    body = {
        "id": video_id,
        "status": {
            "privacyStatus": privacy_status,
            "embeddable": True,
            "license": "youtube",
            "publicStatsViewable": privacy_status == "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    try:
        yt.videos().update(part="status", body=body).execute()
    except HttpError as e:
        logger.warning(
            "youtube.status_update_failed",
            extra={"video_id": video_id, "privacy_status": privacy_status, "detail": str(e)},
        )


def _sync_poll_until_processed_or_terminal(
    yt: Any,
    video_id: str,
    *,
    max_wait_sec: float = _YT_POST_UPLOAD_POLL_MAX_SEC,
) -> tuple[dict[str, Any], str]:
    """
    Poll until the video is playable or YouTube reports a hard failure.

    Returns (video_resource, terminal_reason) where terminal_reason is
    'processed' | 'failed' | 'timeout'.
    """
    start = time.monotonic()
    interval = _YT_POST_UPLOAD_POLL_FIRST_INTERVAL
    last_resource: dict[str, Any] = {}
    while time.monotonic() - start < max_wait_sec:
        try:
            res = _sync_fetch_video_resource(yt, video_id)
        except InsufficientYouTubeReadScope:
            return last_resource, "scope_limited"
        if not res:
            raise RuntimeError(
                f"YouTube returned no video for id={video_id} right after upload. "
                "Confirm the Google account you connected is the same channel you open in Studio, "
                "and that the Data API project has YouTube Data API v3 enabled."
            )
        last_resource = res
        st = last_resource.get("status") or {}
        upload_status = str(st.get("uploadStatus") or "")
        if upload_status == "failed":
            return last_resource, "failed"
        if upload_status == "rejected":
            return last_resource, "failed"
        if upload_status == "deleted":
            raise RuntimeError("YouTube reported the video as deleted during processing.")
        if upload_status == "processed":
            return last_resource, "processed"
        time.sleep(interval)
        interval = min(interval * 1.25, _YT_POST_UPLOAD_POLL_MAX_INTERVAL)
    return last_resource, "timeout"


def _sync_finalize_after_upload(
    creds: Credentials,
    video_id: str,
    privacy_status: str = _YOUTUBE_DEFAULT_PRIVACY,
) -> dict[str, Any]:
    """
    After resumable insert completes: force visibility, wait for transcode or failure,
    attach channel + watch URL metadata for support and UX.

    ``privacy_status`` is propagated end-to-end from the originating
    ``PlatformVariant.metadata_json`` (B-11) — earlier versions hard-coded
    ``"public"`` here, silently overriding any user choice of unlisted/private.
    """
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    try:
        _sync_ensure_visibility_and_embeddable(yt, video_id, privacy_status)
        resource, terminal = _sync_poll_until_processed_or_terminal(yt, video_id)
    except HttpError as e:
        if _youtube_http_is_insufficient_scope(e):
            logger.warning(
                "youtube.finalize_insufficient_scope",
                extra={"video_id": video_id, "hint": "Reconnect YouTube to grant the youtube scope."},
            )
            return {
                "youtube_watch_url": _youtube_watch_url(video_id),
                "youtube_finalize_skipped": True,
                "youtube_needs_reconnect_for_readonly": True,
                "youtube_upload_status": None,
                "youtube_processing_complete": False,
                "youtube_transcode_pending": True,
            }
        raise
    if terminal == "scope_limited":
        logger.warning(
            "youtube.finalize_read_scope_missing",
            extra={
                "video_id": video_id,
                "hint": "Reconnect YouTube so the token includes the youtube scope (post-upload poll).",
            },
        )
        return {
            "youtube_watch_url": _youtube_watch_url(video_id),
            "youtube_finalize_skipped": True,
            "youtube_needs_reconnect_for_readonly": True,
            "youtube_upload_status": None,
            "youtube_processing_complete": False,
            "youtube_transcode_pending": True,
        }
    if terminal == "failed":
        msg = _format_youtube_failure(resource)
        raise RuntimeError(f"YouTube failed to process this file: {msg}")
    if terminal == "timeout":
        logger.warning(
            "youtube.upload.transcode_pending",
            extra={
                "video_id": video_id,
                "upload_status": (resource.get("status") or {}).get("uploadStatus"),
                "hint": "Still transcoding; it can take several minutes for long videos.",
            },
        )
    _sync_ensure_visibility_and_embeddable(yt, video_id, privacy_status)

    meta: dict[str, Any] = {
        "youtube_watch_url": _youtube_watch_url(video_id),
        "youtube_upload_status": (resource.get("status") or {}).get("uploadStatus"),
        "youtube_processing_complete": terminal == "processed",
        "youtube_transcode_pending": terminal == "timeout",
    }
    snip = resource.get("snippet") or {}
    if snip.get("channelId"):
        meta["youtube_video_channel_id"] = snip.get("channelId")
    try:
        ch = yt.channels().list(part="snippet", mine=True, maxResults=1).execute()
        items = ch.get("items") or []
        if items:
            meta["youtube_channel_id"] = items[0].get("id")
            meta["youtube_channel_title"] = (items[0].get("snippet") or {}).get("title")
    except HttpError as e:
        if _youtube_http_is_insufficient_scope(e):
            meta["youtube_needs_reconnect_for_readonly"] = True
    return meta


class YouTubePublisher:
    """
    Native YouTube publisher (OAuth).

    NOTE: This publisher is intended to be called from within an async DB session context.
    """

    PLATFORM = "youtube"
    # "YouTube Data API v3" uploads cost ~1600 units.
    UNITS_PER_UPLOAD = 1600

    def __init__(self) -> None:
        pass

    async def upload_video(self, platform_variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa = await self._load_variant_and_account(platform_variant_id, db)
        creds = await self._build_credentials(sa, db)

        privacy_status = _resolve_youtube_privacy(pv)
        video_id = await self._upload_video_resumable(pv, creds, is_short=False, privacy_status=privacy_status)
        finalize_meta = await asyncio.to_thread(_sync_finalize_after_upload, creds, video_id, privacy_status)

        # Optional thumbnail from metadata
        thumbnail_url = None
        if isinstance(pv.metadata_json, dict):
            thumbnail_url = pv.metadata_json.get("thumbnail_url")
        if thumbnail_url:
            try:
                await self.upload_thumbnail(video_id=video_id, thumbnail_url=str(thumbnail_url), credentials=creds)
            except Exception:
                # Thumbnail is best-effort
                pass

        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {
            **(pv.metadata_json or {}),
            "youtube_video_id": video_id,
            "privacy_status": privacy_status,
            **finalize_meta,
        }
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="youtube_upload",
                details={
                    "platform_variant_id": str(pv.id),
                    "youtube_video_id": video_id,
                    "units": self.UNITS_PER_UPLOAD,
                    **{k: finalize_meta[k] for k in ("youtube_watch_url", "youtube_channel_title") if k in finalize_meta},
                },
            )
        )
        await db.commit()
        out: dict[str, Any] = {"ok": True, "youtube_video_id": video_id}
        wurl = finalize_meta.get("youtube_watch_url")
        if wurl:
            out["youtube_watch_url"] = wurl
        return out

    async def upload_short(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa = await self._load_variant_and_account(variant_id, db)
        creds = await self._build_credentials(sa, db)

        privacy_status = _resolve_youtube_privacy(pv)
        video_id = await self._upload_video_resumable(pv, creds, is_short=True, privacy_status=privacy_status)
        finalize_meta = await asyncio.to_thread(_sync_finalize_after_upload, creds, video_id, privacy_status)

        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {
            **(pv.metadata_json or {}),
            "youtube_video_id": video_id,
            "is_short": True,
            "privacy_status": privacy_status,
            **finalize_meta,
        }
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="youtube_upload_short",
                details={
                    "platform_variant_id": str(pv.id),
                    "youtube_video_id": video_id,
                    "units": self.UNITS_PER_UPLOAD,
                    **{k: finalize_meta[k] for k in ("youtube_watch_url", "youtube_channel_title") if k in finalize_meta},
                },
            )
        )
        await db.commit()
        out: dict[str, Any] = {"ok": True, "youtube_video_id": video_id}
        wurl = finalize_meta.get("youtube_watch_url")
        if wurl:
            out["youtube_watch_url"] = wurl
        return out

    async def upload_thumbnail(self, video_id: str, thumbnail_url: str, credentials: Credentials) -> None:
        yt = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        media: MediaFileUpload | None = None
        with tempfile.TemporaryDirectory(prefix="reforge_yt_thumb_", ignore_cleanup_errors=True) as td:
            path = os.path.join(td, "thumb.jpg")
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                r = await client.get(thumbnail_url)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)

            try:
                media = MediaFileUpload(path, mimetype="image/jpeg", resumable=False)
                yt.thumbnails().set(videoId=video_id, media_body=media).execute()
            finally:
                _close_media_file_upload(media)

    async def check_quota(self, user_id: str, db: AsyncSession) -> dict[str, Any]:
        # Count today's YouTube uploads for the user.
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        stmt = select(func.count(ActivityLog.id)).where(
            ActivityLog.user_id == user_id,
            ActivityLog.action.in_(["youtube_upload", "youtube_upload_short"]),
            ActivityLog.created_at >= start,
            ActivityLog.created_at < end,
        )
        count = int((await db.execute(stmt)).scalar_one() or 0)
        used = count * self.UNITS_PER_UPLOAD
        # Default daily quota is project-specific; we return used + remaining if provided.
        daily_quota = 10_000
        return {"used_units": used, "remaining_units": max(0, daily_quota - used), "daily_quota_units": daily_quota}

    # -------------------------
    # Internals
    # -------------------------

    async def _load_variant_and_account(self, platform_variant_id: str, db: AsyncSession) -> tuple[PlatformVariant, SocialAccount]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == platform_variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != self.PLATFORM:
            raise ValueError("PlatformVariant is not a YouTube variant")

        # Social account lookup by user_id is not directly on PlatformVariant.
        # We infer it via the ContentItem owner stored in metadata when created by pipeline,
        # otherwise fallback to the latest active social account for YouTube.
        # (Content processor currently writes ActivityLog with user_id; you can store user_id
        #  in PlatformVariant.metadata_json later for direct mapping.)
        #
        # Best-effort: if metadata_json has user_id, use it; else fail fast.
        user_id = None
        if isinstance(pv.metadata_json, dict):
            user_id = pv.metadata_json.get("user_id")
        if not user_id:
            # We can't safely infer user_id without joining ContentItem; do that.
            from app.models.content_orm import ContentItem

            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == pv.content_item_id))
            ).scalar_one_or_none()
            if item is None:
                raise ValueError("ContentItem not found for variant")
            user_id = item.user_id

        sa = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.user_id == user_id,
                    SocialAccount.platform == self.PLATFORM,
                    SocialAccount.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if sa is None:
            raise ValueError("No active YouTube SocialAccount connected for user")
        return pv, sa

    async def _build_credentials(self, sa: SocialAccount, db: AsyncSession) -> Credentials:
        access_token = decrypt_token(sa.access_token_encrypted)
        refresh_token = decrypt_token(sa.refresh_token_encrypted)

        if not access_token:
            raise ValueError("Missing YouTube access token")

        import os

        client_id = (settings.GOOGLE_CLIENT_ID or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
        client_secret = (settings.GOOGLE_CLIENT_SECRET or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=list(_YOUTUBE_API_SCOPES),
        )

        if creds.expired and creds.refresh_token:
            req = Request()
            creds.refresh(req)
            sa.access_token_encrypted = encrypt_token(creds.token)
            if creds.expiry:
                sa.token_expires_at = creds.expiry
            db.add(sa)
            await db.commit()
        elif creds.expired:
            raise ValueError("YouTube access expired; reconnect your YouTube account.")

        return creds

    async def _upload_video_resumable(
        self,
        pv: PlatformVariant,
        creds: Credentials,
        *,
        is_short: bool,
        privacy_status: str = _YOUTUBE_DEFAULT_PRIVACY,
    ) -> str:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        if privacy_status not in _YOUTUBE_ALLOWED_PRIVACY:
            privacy_status = _YOUTUBE_DEFAULT_PRIVACY

        if not pv.media_url:
            raise ValueError("PlatformVariant.media_url is required for YouTube upload")

        title = (pv.caption or "").strip() or "ReForge Upload"
        description = ""
        tags: list[str] = []
        if isinstance(pv.metadata_json, dict):
            description = str(pv.metadata_json.get("description") or "")
            tags_raw = pv.metadata_json.get("tags") or []
            if isinstance(tags_raw, list):
                tags = [str(t) for t in tags_raw][:15]

        if is_short:
            if "#shorts" not in title.lower():
                title = f"{title} #Shorts"
            if "#shorts" not in description.lower():
                description = (description + "\n\n#Shorts").strip()

        # Download to temp file — close MediaFileUpload's handle before deleting temp dir (Windows).
        media: MediaFileUpload | None = None
        with tempfile.TemporaryDirectory(prefix="reforge_yt_upload_", ignore_cleanup_errors=True) as td:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                r = await client.get(pv.media_url)
                r.raise_for_status()
                suffix, mimetype = _youtube_upload_suffix_and_mime(str(pv.media_url), r.headers.get("content-type"))
                data = r.content
            path = os.path.join(td, f"upload{suffix}")
            with open(path, "wb") as f:
                f.write(data)

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "22",
                    "defaultLanguage": "en",
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "embeddable": True,
                    "license": "youtube",
                    "publicStatsViewable": privacy_status == "public",
                    "selfDeclaredMadeForKids": False,
                },
            }

            try:
                media = MediaFileUpload(
                    path,
                    mimetype=mimetype,
                    chunksize=50 * 1024 * 1024,
                    resumable=True,
                )
                request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
                response = None
                while response is None:
                    _, response = request.next_chunk()
                video_id = response.get("id")
                if not video_id:
                    raise RuntimeError("YouTube upload finished but video ID missing")
                return str(video_id)
            except HttpError as e:
                # quotaExceeded handling
                if "quotaExceeded" in str(e):
                    pv.status = "failed"
                    pv.error_message = "YouTube quota exceeded"
                    pv.retry_count = (pv.retry_count or 0) + 1
                    # next day midnight UTC
                    now = datetime.now(timezone.utc)
                    next_midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
                    pv.metadata_json = {**(pv.metadata_json or {}), "retry_after": next_midnight.isoformat()}
                    raise
                raise
            finally:
                _close_media_file_upload(media)

