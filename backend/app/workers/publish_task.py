from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any

import httpx
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.activity_orm import ActivityLog
from app.models.content_orm import PlatformVariant
from app.services.media_service import MediaService
from app.services.media_validation import MediaValidationError, probe_url, validate_for_platform
from app.services.publishers.errors import RetryablePublishError
from app.services.publishers.facebook_publisher import FacebookPublisher
from app.services.publishers.instagram_publisher import InstagramPublisher
from app.services.publishers.linkedin_publisher import LinkedInPublisher
from app.services.publishers.twitter_publisher import TwitterPublisher
from app.services.publishers.youtube_publisher import YouTubePublisher
from app.services.publishers.registry import get_publisher_for_platform
from app.workers.async_bridge import run_coroutine_for_celery
from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)

# Resumable uploads can exceed default Celery limits; this task sets its own ceilings.
_PUBLISH_TASK_SOFT_LIMIT_SEC = 25 * 60
_PUBLISH_TASK_HARD_LIMIT_SEC = 30 * 60



def _countdown_for_retry(retries: int) -> int:
    # Backoff 5m -> 15m -> 30m
    minutes = [5, 15, 30]
    return minutes[min(retries, len(minutes) - 1)] * 60


def _is_network_error(exc: BaseException) -> bool:
    # Do not treat OSError broadly: Windows file locking (PermissionError, etc.) during temp cleanup
    # is not a transient network failure and should not trigger long Celery retries.
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            socket.timeout,
            ConnectionError,
            BrokenPipeError,
        ),
    )


async def _publish_variant(platform_variant_id: str) -> dict[str, Any]:
    """Publish one platform variant.

    Concurrency note: this coroutine intentionally holds a single
    ``SessionLocal()`` open across the entire publish call (which can run
    for tens of minutes for video uploads). Splitting it into multiple
    short transactions would require touching every native publisher
    (which expects to share the session passed in via ``db=``) and is out
    of scope for the production-hardening pass. The connection pool size
    in ``app.core.database`` is tuned with this in mind.

    P-6 hardening:
        * ``RetryablePublishError`` is no longer swallowed by the broad
          ``except Exception`` handler. Previously a Twitter / Facebook
          rate-limit (HTTP 429 → ``RetryablePublishError``) was caught
          here, the variant was marked permanently ``failed``, and the
          dict return prevented the outer Celery task from triggering a
          retry. Now we revert the status to the pre-publish value so the
          scheduler will re-pick it up, then re-raise so the Celery task
          decorator triggers ``self.retry(countdown=...)`` with the
          publisher-supplied delay.
        * Every log line carries a stable correlation extras block
          ``{platform_variant_id, platform}`` so triaging a wave of
          publish failures no longer requires guessing which task touched
          which variant.
    """
    log_extra = {"platform_variant_id": platform_variant_id}

    async with SessionLocal() as db:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == platform_variant_id))).scalar_one_or_none()
        if pv is None:
            logger.warning("publish.variant_not_found", extra=log_extra)
            return {"ok": False, "error": "platform_variant_not_found", "message": "Publish target was not found."}

        platform = (pv.platform or "").lower()
        log_extra = {**log_extra, "platform": platform}

        # Snapshot the pre-publish status so a retryable failure can revert
        # cleanly. Default to "scheduled" if the variant somehow had no
        # status (defensive — DB schema marks it NOT NULL).
        previous_status = pv.status or "scheduled"

        # Mark as publishing early so the frontend can show progress even for native publishers.
        pv.status = "publishing"
        pv.error_message = None
        db.add(pv)
        await db.commit()
        logger.info("publish.start", extra=log_extra)

        # ------------------------------------------------------------------
        # Preflight media validation (fail fast before touching external APIs).
        # ------------------------------------------------------------------
        if pv.media_url:
            try:
                info = await probe_url(str(pv.media_url))
                validate_for_platform(platform, info)
            except MediaValidationError as e:
                try:
                    ms = MediaService()
                    fixed = ms.resize_for_platform(str(pv.media_url), platform, "video")
                    if fixed.url != str(pv.media_url):
                        info2 = await probe_url(fixed.url)
                        validate_for_platform(platform, info2)
                        pv.media_url = fixed.url
                        pv.metadata_json = {**(pv.metadata_json or {}), "media_autofix": True}
                        db.add(pv)
                        await db.commit()
                        logger.info("publish.media_autofix_applied", extra=log_extra)
                    else:
                        raise e
                except MediaValidationError as err:
                    msg = f"Media validation failed: {err}"
                    pv.status = "failed"
                    pv.error_message = msg
                    db.add(pv)
                    await db.commit()
                    logger.warning(
                        "publish.media_invalid",
                        extra={**log_extra, "reason": str(err)[:240]},
                    )
                    return {"ok": False, "error": "media_invalid", "message": msg}

        # ------------------------------------------------------------------
        # Resolve the publisher.
        # ------------------------------------------------------------------
        try:
            publisher = get_publisher_for_platform(pv.platform)
        except Exception as e:
            pv.status = "failed"
            pv.error_message = str(e)
            db.add(pv)
            await db.commit()
            logger.warning(
                "publish.publisher_not_configured",
                extra={**log_extra, "reason": str(e)[:240]},
            )
            return {"ok": False, "error": "publisher_not_configured", "message": str(e)}

        # ------------------------------------------------------------------
        # Native routing.
        #
        # Order of catch blocks is significant:
        #   1. ``RetryablePublishError`` MUST come before ``Exception`` so we
        #      never accidentally promote a transient rate-limit to a
        #      permanent ``failed`` status. We revert the variant to its
        #      pre-publish state so the next retry will re-enter
        #      ``mark_publishing`` cleanly, then re-raise so the outer
        #      ``publish_content_task`` triggers ``self.retry(countdown=...)``
        #      with the publisher-supplied countdown.
        # ------------------------------------------------------------------
        try:
            if isinstance(publisher, YouTubePublisher):
                meta = pv.metadata_json if isinstance(pv.metadata_json, dict) else {}
                if meta.get("is_short"):
                    return await publisher.upload_short(str(pv.id), db)
                return await publisher.upload_video(str(pv.id), db)
            if isinstance(publisher, FacebookPublisher):
                meta = pv.metadata_json if isinstance(pv.metadata_json, dict) else {}
                if meta.get("is_reel"):
                    return await publisher.publish_reel(str(pv.id), db)
                u = (pv.media_url or "").lower().split("?")[0]
                if any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    return await publisher.publish_photo(str(pv.id), db)
                return await publisher.publish_video(str(pv.id), db)
            if isinstance(publisher, InstagramPublisher):
                meta = pv.metadata_json if isinstance(pv.metadata_json, dict) else {}
                if meta.get("is_reel"):
                    return await publisher.publish_reel(str(pv.id), db)
                return await publisher.publish_feed_or_video(str(pv.id), db)
            if isinstance(publisher, LinkedInPublisher):
                return await publisher.publish_post(str(pv.id), db)
            if isinstance(publisher, TwitterPublisher):
                thread = None
                if isinstance(pv.metadata_json, dict):
                    thread = pv.metadata_json.get("thread_tweets")
                if isinstance(thread, list) and thread:
                    return await publisher.post_thread(str(pv.id), db)
                return await publisher.post_tweet(str(pv.id), db)
        except RetryablePublishError as exc:
            # Revert to pre-publish status so the retry path is clean. Do
            # NOT mark "failed" — the upstream is asking us to back off,
            # not telling us the post is invalid.
            pv_reload = (
                await db.execute(select(PlatformVariant).where(PlatformVariant.id == pv.id))
            ).scalar_one_or_none()
            if pv_reload is not None:
                pv_reload.status = previous_status
                pv_reload.error_message = (
                    f"retry_after={exc.retry_after_seconds}s: {str(exc)[:240]}"
                )
                pv_reload.retry_count = (pv_reload.retry_count or 0) + 1
                db.add(pv_reload)
                await db.commit()
            logger.warning(
                "publish.retryable",
                extra={
                    **log_extra,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "reason": str(exc)[:240],
                },
            )
            # Re-raise so publish_content_task.except RetryablePublishError fires.
            raise
        except Exception as exc:
            msg = str(exc)[:5000]
            logger.exception(
                "publish.native_publish_exception",
                extra={**log_extra, "exc_type": type(exc).__name__},
            )
            pv_reload = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == pv.id))).scalar_one_or_none()
            if pv_reload is not None:
                pv_reload.status = "failed"
                pv_reload.error_message = msg
                db.add(pv_reload)
                await db.commit()
            return {"ok": False, "error": "publish_failed", "message": msg}

        # ------------------------------------------------------------------
        # Generic webhook publisher fallback.
        # ------------------------------------------------------------------
        payload = {
            "platform": pv.platform,
            "caption": pv.caption,
            "hashtags": pv.hashtags,
            "metadata": pv.metadata_json,
            "media_url": pv.media_url,
            "scheduled_at": pv.scheduled_at.isoformat() if pv.scheduled_at else None,
        }

        try:
            result = await publisher.publish(payload)
        except RetryablePublishError as exc:
            # Same contract as the native path: revert + re-raise.
            pv_reload = (
                await db.execute(select(PlatformVariant).where(PlatformVariant.id == pv.id))
            ).scalar_one_or_none()
            if pv_reload is not None:
                pv_reload.status = previous_status
                pv_reload.error_message = (
                    f"retry_after={exc.retry_after_seconds}s: {str(exc)[:240]}"
                )
                pv_reload.retry_count = (pv_reload.retry_count or 0) + 1
                db.add(pv_reload)
                await db.commit()
            logger.warning(
                "publish.retryable_generic",
                extra={
                    **log_extra,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "reason": str(exc)[:240],
                },
            )
            raise

        pv.status = "published"
        pv.published_at = datetime.now(timezone.utc)
        pv.error_message = None
        db.add(pv)

        db.add(
            ActivityLog(
                user_id=None,
                content_item_id=pv.content_item_id,
                action="platform_published",
                details={"platform": pv.platform, "provider": result.provider, "url": result.url},
            )
        )

        await db.commit()
        logger.info(
            "publish.success",
            extra={**log_extra, "provider": result.provider},
        )
        return {"ok": True, "platform_variant_id": str(pv.id)}


@celery_app.task(
    bind=True,
    max_retries=3,
    name="reforge.publish_content",
    soft_time_limit=_PUBLISH_TASK_SOFT_LIMIT_SEC,
    time_limit=_PUBLISH_TASK_HARD_LIMIT_SEC,
)
def publish_content_task(self, platform_variant_id: str):
    task_id = getattr(self.request, "id", None)
    retries = int(getattr(self.request, "retries", 0) or 0)
    log_extra = {
        "platform_variant_id": platform_variant_id,
        "task_id": task_id,
        "retries": retries,
    }
    try:
        logger.info("publish_content_task.start", extra=log_extra)
        return run_coroutine_for_celery(
            _publish_variant(platform_variant_id=platform_variant_id),
            timeout_sec=_PUBLISH_TASK_HARD_LIMIT_SEC,
        )
    except Exception as exc:
        if isinstance(exc, RetryablePublishError):
            logger.warning(
                "publish_content_task.retryable",
                extra={
                    **log_extra,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "reason": str(exc)[:240],
                },
            )
            raise self.retry(exc=exc, countdown=int(exc.retry_after_seconds))
        if _is_network_error(exc):
            countdown = _countdown_for_retry(retries)
            logger.warning(
                "publish_content_task.network_retry",
                extra={
                    **log_extra,
                    "countdown_seconds": countdown,
                    "exc_type": type(exc).__name__,
                },
            )
            raise self.retry(exc=exc, countdown=countdown)

        logger.exception(
            "publish_content_task.terminal_failure",
            extra={**log_extra, "exc_type": type(exc).__name__},
        )
        raise

