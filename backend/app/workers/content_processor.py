from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

from celery import chain
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.fallbacks import get_all_platform_fallbacks
from app.models.activity_orm import ActivityLog
from app.models.content_orm import ContentItem, PlatformVariant
from app.services.ai_service import AIService
from app.services.media_service import MediaService
from app.services.prompt_templates import validate_output
from app.services.gemini_service import GeminiService
from app.workers.async_bridge import run_coroutine_for_celery
from app.workers.celery_app import celery_app

# B-4: ``AIService`` is the legacy two-step (analyze -> generate) pipeline used
# by ``_analyze_only`` and ``_generate_after``. The import was previously
# missing, which made both helpers a latent ``NameError`` the moment any
# caller — including the chained ``generate_variants_task`` whenever an
# ``analyze_result`` carries the legacy ``{"ok": True, ...}`` shape — invoked
# them. The live happy path is in ``analyze_media_task`` (which uses
# ``GeminiService`` directly), but the legacy helpers must still be runnable
# because they are reachable from the existing chained Celery task.

logger = get_task_logger(__name__)


async def _mark_item_failed(content_item_id: str, user_id: str, reason: str) -> None:
    async with SessionLocal() as db:
        item = (
            await db.execute(select(ContentItem).where(ContentItem.id == content_item_id, ContentItem.user_id == user_id))
        ).scalar_one_or_none()
        if item:
            item.status = "failed"
            db.add(item)
            db.add(
                ActivityLog(
                    user_id=item.user_id,
                    content_item_id=item.id,
                    action="content_processing_failed",
                    details={"error": reason[:2000]},
                )
            )
            await db.commit()


async def _analyze_only(content_item_id: str, user_id: str) -> dict[str, Any]:
    media_service = MediaService()

    async with SessionLocal() as db:
        item = (
            await db.execute(
                select(ContentItem).where(ContentItem.id == content_item_id, ContentItem.user_id == user_id)
            )
        ).scalar_one_or_none()
        if item is None:
            return {"ok": False, "error": "content_item_not_found"}
        if not item.original_file_url:
            return {"ok": False, "error": "missing_original_file_url"}

        item.status = "processing"
        db.add(item)
        await db.commit()
        detected = media_service.detect_format(item.original_file_url, item.file_type)
        file_url = item.original_file_url

    try:
        ai_service = AIService()
        analysis = await asyncio.to_thread(ai_service.analyze_media, file_url, detected)
        return {
            "ok": True,
            "content_item_id": content_item_id,
            "user_id": user_id,
            "detected_format": detected,
            "analysis": json.loads(analysis.model_dump_json()),
            "file_url": file_url,
        }
    except Exception as e:
        await _mark_item_failed(content_item_id, user_id, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"ai_analyze_failed: {type(e).__name__}: {e}"}


async def _generate_after(prev: dict[str, Any]) -> dict[str, Any]:
    if not prev.get("ok"):
        return prev

    content_item_id = prev["content_item_id"]
    user_id = prev["user_id"]

    from app.services.ai_types import MediaAnalysis

    ai_service = AIService()
    analysis_obj = MediaAnalysis.model_validate(prev["analysis"])
    variants = await ai_service.generate_platform_variants(analysis_obj, user_prefs={})

    media_service = MediaService()
    detected = prev.get("detected_format") or "image"

    async with SessionLocal() as db:
        item = (
            await db.execute(
                select(ContentItem).where(ContentItem.id == content_item_id, ContentItem.user_id == user_id)
            )
        ).scalar_one_or_none()

        if item is None:
            return {"ok": False, "error": "content_item_not_found"}

        resized = {
            p: media_service.resize_for_platform(item.original_file_url, p, detected).url
            for p in ["youtube", "instagram", "twitter", "linkedin", "facebook"]
        }

        # Replace any prior variants in a single transaction for consistency.
        await db.execute(delete(PlatformVariant).where(PlatformVariant.content_item_id == item.id))

        pvs: list[PlatformVariant] = []
        for platform, pc in variants.items():
            payload = pc.payload or {}

            if platform == "youtube":
                caption = payload.get("title")
                hashtags: list[Any] = []
                desc = str(payload.get("description") or "")
                title_s = str(caption or "")
                is_short = "shorts" in title_s.lower() or "#shorts" in desc.lower()
                metadata = {
                    "description": payload.get("description"),
                    "tags": payload.get("tags"),
                    "is_short": is_short,
                }
            elif platform == "instagram":
                caption = payload.get("caption")
                hashtags = payload.get("hashtags") or []
                metadata = {"story_text": payload.get("story_text"), "is_reel": False}
            elif platform == "twitter":
                caption = payload.get("tweet")
                hashtags = []
                metadata = {"thread_tweets": payload.get("thread")}
            elif platform == "linkedin":
                caption = payload.get("post")
                hashtags = payload.get("hashtags") or []
                metadata = {}
            else:
                caption = payload.get("post")
                hashtags = payload.get("hashtags") or []
                metadata = {"is_reel": False}

            if platform == "youtube":
                vr = validate_output("youtube", {"title": str(caption or "")})
            elif platform == "twitter":
                vr = validate_output("twitter", {"tweet": str(caption or "")})
            elif platform == "facebook":
                vr = validate_output("facebook", {"post": str(caption or "")})
            elif platform == "instagram":
                vr = validate_output("instagram", {"caption": str(caption or "")})
            else:
                vr = validate_output("linkedin", {"post": str(caption or "")})

            error_message = None if vr.is_valid else "; ".join(vr.violations)
            pst = "scheduled" if platform in {"youtube", "facebook", "twitter"} else "assisted"

            pv = PlatformVariant(
                content_item_id=item.id,
                platform=platform,
                caption=str(caption or "").strip() or None,
                hashtags=hashtags,
                metadata_json=metadata,
                media_url=resized.get(platform),
                scheduled_at=item.scheduled_at,
                status=pst,
                error_message=error_message,
                retry_count=0,
            )
            pvs.append(pv)

        db.add_all(pvs)
        await db.flush()
        created_ids = [str(pv.id) for pv in pvs]

        item.status = "scheduled"
        db.add(item)
        db.add(
            ActivityLog(
                user_id=item.user_id,
                content_item_id=item.id,
                action="content_processed",
                details={"platform_variants_created": created_ids, "detected_format": detected},
            )
        )

        await db.commit()

        return {"ok": True, "platform_variant_ids": created_ids}


def _emergency_fallback_save(content_item_id: str) -> None:
    """
    Best-effort fallback persistence. Never raises.
    """

    async def _save() -> None:
        fallbacks = get_all_platform_fallbacks()
        async with SessionLocal() as db:
            item = (await db.execute(select(ContentItem).where(ContentItem.id == content_item_id))).scalar_one_or_none()
            if item is None:
                return

            await db.execute(delete(PlatformVariant).where(PlatformVariant.content_item_id == item.id))

            pvs: list[PlatformVariant] = []
            for platform in ["youtube", "instagram", "twitter", "linkedin", "facebook"]:
                payload = fallbacks.get(platform) or {}
                if platform == "youtube":
                    caption = payload.get("title")
                    hashtags: list[Any] = []
                    desc = str(payload.get("description") or "")
                    title_s = str(caption or "")
                    is_short = "shorts" in title_s.lower() or "#shorts" in desc.lower()
                    metadata = {
                        "description": payload.get("description"),
                        "tags": payload.get("tags"),
                        "is_short": is_short,
                    }
                elif platform == "instagram":
                    caption = payload.get("caption")
                    hashtags = payload.get("hashtags") or []
                    metadata = {"story_text": payload.get("story_text"), "is_reel": False}
                elif platform == "twitter":
                    caption = payload.get("tweet")
                    hashtags = []
                    metadata = {"thread_tweets": payload.get("thread")}
                elif platform == "linkedin":
                    caption = payload.get("post")
                    hashtags = payload.get("hashtags") or []
                    metadata = {}
                else:
                    caption = payload.get("post")
                    hashtags = payload.get("hashtags") or []
                    metadata = {"is_reel": False}

                pv = PlatformVariant(
                    content_item_id=item.id,
                    platform=platform,
                    caption=str(caption or "").strip() or None,
                    hashtags=hashtags,
                    metadata_json=metadata,
                    media_url=None,
                    scheduled_at=item.scheduled_at,
                    status="assisted" if platform in {"instagram", "linkedin"} else "scheduled",
                    error_message="fallback",
                    retry_count=0,
                )
                if hasattr(pv, "is_fallback"):
                    setattr(pv, "is_fallback", True)
                pvs.append(pv)

            db.add_all(pvs)
            item.status = "error_fallback"
            db.add(item)
            await db.commit()

    try:
        run_coroutine_for_celery(_save(), timeout_sec=35)
    except Exception as exc:
        logger.critical("emergency fallback save failed", extra={"content_id": content_item_id, "exc_type": type(exc).__name__})


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=3,
    # Video + multi-model Gemini/OpenRouter retries can exceed prior 60s Celery ceilings.
    soft_time_limit=8 * 60,
    time_limit=9 * 60,
    queue="high_priority",
    name="reforge.content_analyze",
)
def analyze_media_task(self, content_item_id: str, user_id: str) -> dict[str, Any]:
    logger.info(
        "content analyze task started",
        extra={"content_id": content_item_id, "task_id": getattr(self.request, "id", None)},
    )

    try:
        # Step 2: best-effort status update + fetch URL
        image_url: str | None = None

        async def _mark_processing_and_get_url() -> str | None:
            async with SessionLocal() as db:
                item = (
                    await db.execute(
                        select(ContentItem).where(ContentItem.id == content_item_id, ContentItem.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if item is None:
                    return None
                item.status = "processing"
                db.add(item)
                await db.commit()
                return item.original_file_url

        try:
            image_url = run_coroutine_for_celery(_mark_processing_and_get_url(), timeout_sec=10)
        except Exception as exc:
            logger.warning(
                "failed to set processing status",
                extra={"content_id": content_item_id, "exc_type": type(exc).__name__},
            )

        if not image_url:
            _emergency_fallback_save(content_item_id)
            return {"status": "error_fallback", "content_id": content_item_id}

        # Step 3: call GeminiService safely from sync Celery context
        service = GeminiService()
        result = run_coroutine_for_celery(
            service.analyze_and_generate(image_url, content_item_id),
            timeout_sec=6 * 60,
        )

        # Step 4
        is_fallback = "fallback_reason" in (result or {})
        fallback_reason = (result or {}).get("fallback_reason") if is_fallback else None

        # B-9: log the originating provider in one well-shaped line so
        # operators can tell — without log spelunking — whether each task
        # produced real AI content, OpenRouter fallback content, or static
        # platform content. ``GeminiService`` and ``OpenRouterService``
        # already emit ``ai.provider.used`` from inside the call; this
        # higher-level line ties the result back to the Celery task id.
        provider_label = "static_fallback" if is_fallback else "ai_generated"
        logger.info(
            "ai.task.result content_id=%s provider=%s fallback_reason=%s",
            content_item_id,
            provider_label,
            fallback_reason or "",
            extra={
                "event": "ai.task.result",
                "content_id": content_item_id,
                "provider": provider_label,
                "fallback_reason": fallback_reason,
            },
        )

        # Step 5/6/7: build variants + batch save in one transaction
        fallbacks = get_all_platform_fallbacks()
        media_service = MediaService()

        async def _save_variants_and_status() -> str:
            async with SessionLocal() as db:
                item = (
                    await db.execute(
                        select(ContentItem).where(ContentItem.id == content_item_id, ContentItem.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if item is None:
                    return "error_fallback"

                detected = media_service.detect_format(item.original_file_url or "", item.file_type)
                resized = {
                    p: media_service.resize_for_platform(item.original_file_url, p, detected).url
                    for p in ["youtube", "instagram", "twitter", "linkedin", "facebook"]
                }

                await db.execute(delete(PlatformVariant).where(PlatformVariant.content_item_id == item.id))

                pvs: list[PlatformVariant] = []
                for platform in ["youtube", "instagram", "twitter", "linkedin", "facebook"]:
                    payload = (result.get(platform) if isinstance(result, dict) else None) or fallbacks[platform]

                    if platform == "youtube":
                        caption = payload.get("title")
                        hashtags: list[Any] = []
                        desc = str(payload.get("description") or "")
                        title_s = str(caption or "")
                        is_short = "shorts" in title_s.lower() or "#shorts" in desc.lower()
                        metadata = {
                            "description": payload.get("description"),
                            "tags": payload.get("tags"),
                            "is_short": is_short,
                        }
                    elif platform == "instagram":
                        caption = payload.get("caption")
                        hashtags = payload.get("hashtags") or []
                        metadata = {"story_text": payload.get("story_text"), "is_reel": False}
                    elif platform == "twitter":
                        caption = payload.get("tweet")
                        hashtags = []
                        metadata = {"thread_tweets": payload.get("thread")}
                    elif platform == "linkedin":
                        caption = payload.get("post")
                        hashtags = payload.get("hashtags") or []
                        metadata = {}
                    else:
                        caption = payload.get("post")
                        hashtags = payload.get("hashtags") or []
                        metadata = {"is_reel": False}

                    if platform == "youtube":
                        vr = validate_output("youtube", {"title": str(caption or "")})
                    elif platform == "twitter":
                        vr = validate_output("twitter", {"tweet": str(caption or "")})
                    elif platform == "facebook":
                        vr = validate_output("facebook", {"post": str(caption or "")})
                    elif platform == "instagram":
                        vr = validate_output("instagram", {"caption": str(caption or "")})
                    else:
                        vr = validate_output("linkedin", {"post": str(caption or "")})

                    error_message = None if vr.is_valid else "; ".join(vr.violations)
                    pst = "scheduled" if platform in {"youtube", "facebook", "twitter"} else "assisted"

                    pv = PlatformVariant(
                        content_item_id=item.id,
                        platform=platform,
                        caption=str(caption or "").strip() or None,
                        hashtags=hashtags,
                        metadata_json=metadata,
                        media_url=resized.get(platform),
                        scheduled_at=item.scheduled_at,
                        status=pst,
                        error_message=error_message,
                        retry_count=0,
                    )
                    if hasattr(pv, "is_fallback"):
                        setattr(pv, "is_fallback", bool(is_fallback))
                    pvs.append(pv)

                db.add_all(pvs)

                final_status = "completed_fallback" if is_fallback else "completed"
                item.status = final_status
                db.add(item)
                db.add(
                    ActivityLog(
                        user_id=item.user_id,
                        content_item_id=item.id,
                        action="content_processed",
                        details={
                            "is_fallback": bool(is_fallback),
                            # B-9: include the *reason* for the fallback so
                            # operators triaging a wave of completed_fallback
                            # rows can tell at a glance whether the cause was
                            # ``no_api_key`` (config issue), ``circuit_breaker_open``
                            # (upstream outage), ``all_models_exhausted`` (quota),
                            # ``unsupported_media_type``, etc.
                            "fallback_reason": fallback_reason,
                            "image_analysis": (result or {}).get("image_analysis"),
                        },
                    )
                )
                await db.commit()
                return final_status

        final_status = run_coroutine_for_celery(_save_variants_and_status(), timeout_sec=180)

        logger.info(
            "content analyze task completed",
            extra={"content_id": content_item_id, "task_id": getattr(self.request, "id", None), "status": final_status},
        )
        return {"status": final_status, "content_id": content_item_id}

    except SoftTimeLimitExceeded:
        logger.error(
            "content analyze task soft time limit exceeded",
            extra={"content_id": content_item_id, "task_id": getattr(self.request, "id", None)},
        )
        _emergency_fallback_save(content_item_id)
        return {"status": "timeout_fallback", "content_id": content_item_id}
    except Exception as exc:
        logger.exception(
            "content analyze task failed",
            extra={"content_id": content_item_id, "task_id": getattr(self.request, "id", None)},
        )
        if getattr(self.request, "retries", 0) < getattr(self, "max_retries", 2):
            raise self.retry(exc=exc, countdown=3)
        _emergency_fallback_save(content_item_id)
        return {"status": "error_fallback", "content_id": content_item_id}


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=3,
    soft_time_limit=45,
    time_limit=60,
    queue="high_priority",
    name="reforge.content_generate_variants",
)
def generate_variants_task(self, analyze_result: dict[str, Any]) -> dict[str, Any]:
    self.update_state(
        state="PROGRESS",
        meta={
            "phase": "generate_variants",
            "pct": 70,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    # Backward compatible: pipeline is now completed in `reforge.content_analyze`.
    if "status" in analyze_result and "content_id" in analyze_result:
        return analyze_result
    if not analyze_result.get("ok"):
        return analyze_result

    try:
        return run_coroutine_for_celery(_generate_after(analyze_result))
    except Exception as exc:
        logger.exception("generate_variants_task raised")
        run_coroutine_for_celery(
            _mark_item_failed(analyze_result["content_item_id"], analyze_result["user_id"], str(exc)),
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def launch_content_processing(content_item_id: str, user_id: str):
    return chain(
        analyze_media_task.s(content_item_id, user_id),
        generate_variants_task.s(),
    ).apply_async()


def start_content_processing_background(content_item_id: str, user_id: str) -> None:
    """
    Local dev uses Celery eager mode: apply_async() runs the full pipeline synchronously.
    Running it on a daemon thread returns HTTP immediately so browsers/proxies do not
    time out with 500 while processing takes 30–120s.
    """

    def _run() -> None:
        try:
            launch_content_processing(content_item_id, user_id)
        except Exception:
            logger.exception(
                "content processing pipeline failed (background)",
                extra={"content_id": content_item_id},
            )

    threading.Thread(
        target=_run,
        name=f"reforge-pipeline-{content_item_id}",
        daemon=True,
    ).start()


@celery_app.task(name="reforge.process_content")
def process_content_task(content_item_id: str, user_id: str) -> dict[str, Any]:
    """
    Lightweight entrypoint for older routes: enqueue the split pipeline and expose async result ids.
    """
    ar = launch_content_processing(content_item_id, user_id)
    return {"runner_task_id": ar.id, "child_ids": ar.children if getattr(ar, "children", None) else []}


def _countdown_for_retry(retries: int) -> int:
    minutes = [5, 15, 30]
    idx = min(retries, len(minutes) - 1)
    return minutes[idx] * 60


@celery_app.task(bind=True, max_retries=3, name="reforge.process_content.monolith")
def process_content_task_monolithic(self, content_item_id: str, user_id: str):
    """Optional monolithic fallback (heavy); prefer chained tasks."""
    try:
        analyze_res = analyze_media_task.apply(args=(content_item_id, user_id)).get(timeout=420)
        if not analyze_res.get("ok"):
            return analyze_res
        return generate_variants_task.apply(args=(analyze_res,)).get(timeout=420)
    except Exception as exc:
        retries = int(getattr(self.request, "retries", 0) or 0)
        raise self.retry(exc=exc, countdown=_countdown_for_retry(retries))
