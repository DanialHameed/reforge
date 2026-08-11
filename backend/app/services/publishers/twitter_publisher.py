from __future__ import annotations

import asyncio
import base64
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
from app.services.publishers.errors import RetryablePublishError
from app.services.token_crypto import decrypt_token, encrypt_token

__all__ = ["RetryablePublishError", "TwitterPublisher"]


# Media types accepted by the Twitter v1.1 media/upload endpoint.
# image/svg+xml is intentionally excluded — Twitter rejects SVG uploads.
_TWITTER_SUPPORTED_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)
_TWITTER_SUPPORTED_VIDEO_MIMES: frozenset[str] = frozenset(
    {"video/mp4", "video/quicktime", "video/webm"}
)


def _resolve_twitter_media_type(media_url: str, response_content_type: str | None) -> str:
    """
    Determine the ``media_type`` value to pass to Twitter's media/upload INIT.

    Resolution order (B-18):
        1. ``Content-Type`` returned by the origin when downloading the asset.
        2. ``mimetypes.guess_type`` against the URL path (handles Cloudinary
           signed URLs that drop Content-Type to ``application/octet-stream``).
        3. ``image/jpeg`` as a final, conservative fallback.

    Earlier versions used a heuristic (``url.endswith((.mp4,.mov,.webm)) or
    size > 5MB``) that mislabeled large PNGs as ``video/mp4`` (Twitter then
    failed FINALIZE because the bytes weren't a valid MP4) and silently
    coerced uncommon video containers to ``image/jpeg`` (which the chunked
    APPEND endpoint flat-out rejects). The new helper relies on actual MIME
    metadata and only falls back to JPEG when nothing more authoritative is
    available.
    """
    ct = (response_content_type or "").split(";")[0].strip().lower()
    if ct in _TWITTER_SUPPORTED_IMAGE_MIMES or ct in _TWITTER_SUPPORTED_VIDEO_MIMES:
        return ct

    base = media_url.split("?", 1)[0]
    guess, _ = mimetypes.guess_type(base)
    if guess:
        guess = guess.split(";")[0].strip().lower()
        if guess in _TWITTER_SUPPORTED_IMAGE_MIMES or guess in _TWITTER_SUPPORTED_VIDEO_MIMES:
            return guess

    # Generic image/* or video/* hints that aren't in the supported sets get
    # mapped to the closest supported representative so Twitter's INIT still
    # accepts them rather than 415-ing on something like ``image/heic``.
    if ct.startswith("video/"):
        return "video/mp4"
    if ct.startswith("image/"):
        return "image/jpeg"
    return "image/jpeg"


def _raise_for_twitter_upload_stage(resp: httpx.Response, stage: str) -> None:
    """
    Raise a diagnosable error for a failed media/upload.json call.

    The bare ``httpx.HTTPStatusError`` from ``raise_for_status()`` only carries the
    status line, not X's JSON error body — so a 403 here always rendered as an opaque
    "Client error '403 Forbidden' for url ..." with no way to tell a stale OAuth scope
    apart from an API access-tier limitation. X returns the real reason in the response
    body (``errors``/``detail``/``title``), so surface that plus the two most common
    causes for this specific endpoint.
    """
    if resp.status_code < 400:
        return
    body_detail = ""
    try:
        data = resp.json()
        if isinstance(data, dict):
            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                parts = [str(e.get("message") or e) for e in errors if isinstance(e, dict) or e]
                body_detail = "; ".join(p for p in parts if p)
            body_detail = body_detail or str(data.get("detail") or data.get("title") or "")
    except Exception:
        body_detail = (resp.text or "")[:300]

    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Twitter media upload rejected at {stage} ({resp.status_code}): {body_detail or 'no detail returned'}. "
            "Most common causes: (1) the connected Twitter/X account's token was issued before the "
            "`media.write` scope was requested — disconnect and reconnect Twitter under Connections; "
            "(2) your X Developer App's API access tier does not include media uploads on "
            "upload.twitter.com/1.1 — check the tier under the 'Keys and tokens' / 'Products' tab at "
            "developer.x.com and upgrade if it shows Free/Essential."
        )
    raise RuntimeError(f"Twitter media upload failed at {stage} ({resp.status_code}): {body_detail or resp.text[:300]}")


class TwitterPublisher:
    PLATFORM = "twitter"
    API_BASE = "https://api.twitter.com"
    UPLOAD_BASE = "https://upload.twitter.com"

    async def post_tweet(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa, access_token = await self._load_variant_and_account(variant_id, db)
        media_id = None
        if pv.media_url:
            media_id = await self.upload_media(pv.media_url, access_token)

        text = (pv.caption or "").strip()[:280]
        payload: dict[str, Any] = {"text": text}
        if media_id:
            payload["media"] = {"media_ids": [media_id]}

        data = await self._post_json(
            url=f"{self.API_BASE}/2/tweets",
            access_token=access_token,
            json_payload=payload,
            db=db,
            sa=sa,
            pv=pv,
        )
        tweet_id = str((data.get("data") or {}).get("id") or "")
        await self._mark_published(pv, db, sa, tweet_ids=[tweet_id])
        return {"ok": True, "tweet_id": tweet_id}

    async def post_thread(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        pv, sa, access_token = await self._load_variant_and_account(variant_id, db)

        thread = []
        if isinstance(pv.metadata_json, dict):
            thread = pv.metadata_json.get("thread_tweets") or pv.metadata_json.get("thread") or []
        if not isinstance(thread, list) or not thread:
            # Fallback to single tweet
            return await self.post_tweet(variant_id, db)

        media_id = None
        if pv.media_url:
            media_id = await self.upload_media(pv.media_url, access_token)

        tweet_ids: list[str] = []
        previous_id: str | None = None
        for i, t in enumerate(thread):
            text = str(t).strip()[:280]
            payload: dict[str, Any] = {"text": text}
            if i == 0 and media_id:
                payload["media"] = {"media_ids": [media_id]}
            if previous_id:
                payload["reply"] = {"in_reply_to_tweet_id": previous_id}

            data = await self._post_json(
                url=f"{self.API_BASE}/2/tweets",
                access_token=access_token,
                json_payload=payload,
                db=db,
                sa=sa,
                pv=pv,
            )
            tid = str((data.get("data") or {}).get("id") or "")
            if not tid:
                break
            tweet_ids.append(tid)
            previous_id = tid

        await self._mark_published(pv, db, sa, tweet_ids=tweet_ids)
        return {"ok": True, "tweet_ids": tweet_ids}

    async def upload_media(self, file_url: str, access_token: str) -> str:
        # Twitter v1.1 upload endpoint (chunked for both images and videos).
        # Uses Bearer per your spec.
        with tempfile.TemporaryDirectory(prefix="reforge_tw_media_") as td:
            path = os.path.join(td, "media.bin")
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                r = await client.get(file_url)
                r.raise_for_status()
                response_content_type = r.headers.get("content-type")
                with open(path, "wb") as f:
                    f.write(r.content)

            size = os.path.getsize(path)
            media_type = _resolve_twitter_media_type(file_url, response_content_type)

            headers = {"authorization": f"Bearer {access_token}"}

            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                # INIT
                init = await client.post(
                    f"{self.UPLOAD_BASE}/1.1/media/upload.json",
                    headers=headers,
                    data={
                        "command": "INIT",
                        "total_bytes": str(size),
                        "media_type": media_type,
                    },
                )
                _raise_for_twitter_upload_stage(init, "INIT")
                media_id = str(init.json().get("media_id_string") or "")
                if not media_id:
                    raise RuntimeError("Twitter INIT did not return media_id_string")

                # APPEND in 5MB chunks
                segment_id = 0
                chunk_size = 5 * 1024 * 1024
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        files = {"media": chunk}
                        append = await client.post(
                            f"{self.UPLOAD_BASE}/1.1/media/upload.json",
                            headers=headers,
                            data={
                                "command": "APPEND",
                                "media_id": media_id,
                                "segment_index": str(segment_id),
                            },
                            files=files,
                        )
                        _raise_for_twitter_upload_stage(append, "APPEND")
                        segment_id += 1

                # FINALIZE
                fin = await client.post(
                    f"{self.UPLOAD_BASE}/1.1/media/upload.json",
                    headers=headers,
                    data={"command": "FINALIZE", "media_id": media_id},
                )
                _raise_for_twitter_upload_stage(fin, "FINALIZE")
                fin_data = fin.json() if fin.headers.get("content-type", "").startswith("application/json") else {}

                # If Twitter returns processing_info, we must poll before posting the tweet.
                processing = fin_data.get("processing_info") if isinstance(fin_data, dict) else None
                if isinstance(processing, dict):
                    state = str(processing.get("state") or "")
                    check_after = int(processing.get("check_after_secs") or 1)
                    while state in ("pending", "in_progress"):
                        await asyncio.sleep(max(1, check_after))
                        st = await client.get(
                            f"{self.UPLOAD_BASE}/1.1/media/upload.json",
                            headers=headers,
                            params={"command": "STATUS", "media_id": media_id},
                        )
                        _raise_for_twitter_upload_stage(st, "STATUS")
                        st_data = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
                        p2 = st_data.get("processing_info") if isinstance(st_data, dict) else None
                        if not isinstance(p2, dict):
                            break
                        state = str(p2.get("state") or "")
                        check_after = int(p2.get("check_after_secs") or 1)
                        if state == "failed":
                            err = p2.get("error") or {}
                            raise RuntimeError(f"Twitter media processing failed: {err}")

                return media_id

    async def refresh_token(self, social_account: SocialAccount, db: AsyncSession) -> str:
        refresh = decrypt_token(social_account.refresh_token_encrypted)
        if not refresh:
            raise ValueError("Missing Twitter refresh token")
        if not settings.TWITTER_CLIENT_ID or not settings.TWITTER_CLIENT_SECRET:
            raise ValueError("Missing TWITTER_CLIENT_ID / TWITTER_CLIENT_SECRET for refresh flow")

        basic = base64.b64encode(
            f"{settings.TWITTER_CLIENT_ID}:{settings.TWITTER_CLIENT_SECRET}".encode("utf-8")
        ).decode("utf-8")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.post(
                f"{self.API_BASE}/2/oauth2/token",
                headers={"authorization": f"Basic {basic}", "content-type": "application/x-www-form-urlencoded"},
                data={"grant_type": "refresh_token", "refresh_token": refresh},
            )
            r.raise_for_status()
            data = r.json()

        access = str(data.get("access_token") or "")
        new_refresh = data.get("refresh_token")
        expires_in = int(data.get("expires_in") or 0)

        social_account.access_token_encrypted = encrypt_token(access)
        if new_refresh:
            social_account.refresh_token_encrypted = encrypt_token(str(new_refresh))
        if expires_in:
            social_account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        db.add(social_account)
        await db.commit()
        return access

    # -------------------------
    # Internals
    # -------------------------

    async def _load_variant_and_account(self, variant_id: str, db: AsyncSession) -> tuple[PlatformVariant, SocialAccount, str]:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == variant_id))).scalar_one_or_none()
        if pv is None:
            raise ValueError("PlatformVariant not found")
        if (pv.platform or "").lower() != self.PLATFORM:
            raise ValueError("PlatformVariant is not a Twitter variant")

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
            raise ValueError("No active Twitter SocialAccount connected for user")

        access = decrypt_token(sa.access_token_encrypted)
        if not access:
            raise ValueError("Missing Twitter access token")

        # Refresh if expired
        expires = sa.token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and expires <= datetime.now(timezone.utc):
            access = await self.refresh_token(sa, db)

        return pv, sa, access

    async def _post_json(
        self,
        *,
        url: str,
        access_token: str,
        json_payload: dict[str, Any],
        db: AsyncSession,
        sa: SocialAccount,
        pv: PlatformVariant,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.post(url, headers={"authorization": f"Bearer {access_token}"}, json=json_payload)

        # Rate limit
        if r.status_code == 429:
            reset = r.headers.get("x-rate-limit-reset")
            retry_after = 3600
            if reset and reset.isdigit():
                ts = int(reset)
                retry_after = max(60, ts - int(datetime.now(timezone.utc).timestamp()))
            db.add(
                ActivityLog(
                    user_id=sa.user_id,
                    content_item_id=pv.content_item_id,
                    action="twitter_rate_limited",
                    details={"retry_after_seconds": retry_after, "status_code": 429},
                )
            )
            await db.commit()
            raise RetryablePublishError("Twitter rate limit", retry_after_seconds=retry_after)

        # Duplicate content
        if r.status_code == 403 and "duplicate" in (r.text or "").lower():
            pv.status = "failed"
            pv.error_message = "Twitter rejected duplicate content"
            db.add(pv)
            db.add(
                ActivityLog(
                    user_id=sa.user_id,
                    content_item_id=pv.content_item_id,
                    action="twitter_duplicate_content",
                    details={"status_code": 403, "body": r.text[:500]},
                )
            )
            await db.commit()
            return {"ok": False}

        r.raise_for_status()
        return r.json()

    async def _mark_published(self, pv: PlatformVariant, db: AsyncSession, sa: SocialAccount, *, tweet_ids: list[str]) -> None:
        pv.status = "published"
        pv.error_message = None
        pv.published_at = datetime.now(timezone.utc)
        pv.metadata_json = {**(pv.metadata_json or {}), "tweet_ids": tweet_ids}
        db.add(pv)
        db.add(
            ActivityLog(
                user_id=sa.user_id,
                content_item_id=pv.content_item_id,
                action="twitter_published",
                details={"platform_variant_id": str(pv.id), "tweet_ids": tweet_ids},
            )
        )
        await db.commit()

