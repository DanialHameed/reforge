from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.content_orm import ContentItem, PlatformVariant
from app.models.social_orm import SocialAccount
from app.services.token_crypto import try_decrypt_token
from app.workers.publish_task import _PUBLISH_TASK_HARD_LIMIT_SEC, publish_content_task

logger = logging.getLogger(__name__)

# Must exceed Celery publish hard limit so AsyncResult.get() does not truncate long YouTube uploads.
_EAGER_PUBLISH_GET_TIMEOUT_SEC = float(_PUBLISH_TASK_HARD_LIMIT_SEC) + 180.0


async def publish_to_all_platforms(
    user_id: str,
    content_id: str,
    platforms: list[str] | None = None,
    *,
    acknowledge_placeholder_captions: bool = False,
) -> dict[str, Any]:
    """
    One-tap master dispatcher.

    Broadcasts a content item to all connected platforms for the user.
    Uses asyncio.gather(return_exceptions=True) so partial failures don't block other platforms.
    """
    user_uuid = UUID(str(user_id))

    async with SessionLocal() as db:
        accounts = (
            await db.execute(select(SocialAccount).where(SocialAccount.user_id == user_uuid, SocialAccount.is_active.is_(True)))
        ).scalars().all()
        item = (
            await db.execute(
                select(ContentItem)
                .options(selectinload(ContentItem.platform_variants))
                .where(ContentItem.id == content_id, ContentItem.user_id == user_uuid)
            )
        ).scalar_one_or_none()

    if item is None:
        return {"dispatched_to": [], "errors": [{"platform": "all", "error": "content_not_found"}]}

    GENERIC_FALLBACK_SIGNALS = [
        "amazing content worth sharing",
        "just dropped some amazing content",
        "check out this amazing content",
        "excited to share this piece of content with my network. quality work speaks for itself",
        "amazing content you need to see",
        "incredible content created with reforge",
    ]

    def _is_fallback_content(text: str) -> bool:
        text_lower = text.lower().strip()
        return any(sig in text_lower for sig in GENERIC_FALLBACK_SIGNALS)

    variants_by_platform: dict[str, PlatformVariant] = {}
    for v in item.platform_variants or []:
        pk = (v.platform or "").lower().strip()
        if pk == "x":
            pk = "twitter"
        variants_by_platform[pk] = v

    # Check if content is fallback before publishing
    content_is_fallback = False
    for platform_key in ["instagram", "twitter", "linkedin", "facebook", "youtube"]:
        pv = variants_by_platform.get(platform_key)
        if pv is None:
            continue
        main_text = (pv.caption or "").strip()
        if platform_key == "youtube" and isinstance(pv.metadata_json, dict):
            desc = str(pv.metadata_json.get("description") or "").strip()
            main_text = f"{main_text} {desc}".strip()
        if not main_text:
            continue
        if _is_fallback_content(main_text):
            content_is_fallback = True
            break

    if content_is_fallback and not acknowledge_placeholder_captions:
        logger.warning(
            "master_publisher.blocked_fallback",
            extra={"content_id": str(content_id)},
        )
        return {
            "status": "blocked",
            "message": "Placeholder/generic captions detected. Acknowledge on the content page or re-process.",
            "platforms": {},
            "is_fallback": True,
        }

    if content_is_fallback and acknowledge_placeholder_captions:
        logger.info(
            "master_publisher.fallback_publish_acknowledged",
            extra={"content_id": str(content_id)},
        )

    # Treat a social account as active if it has any usable token material.
    active: list[SocialAccount] = []
    for a in accounts:
        access = try_decrypt_token(a.access_token_encrypted) if a else None
        refresh = try_decrypt_token(a.refresh_token_encrypted) if a else None
        if access or refresh:
            active.append(a)

    connected = sorted({(a.platform or "").lower() for a in active if (a.platform or "").strip()})
    # Normalize "x" -> "twitter" on the *requested* side too so a UI that uses
    # the "X" label still resolves to the stored "twitter" SocialAccount. Without
    # this normalization, a publish-selected request with platforms=["x"] used to
    # silently produce zero targets even when Twitter was connected.
    def _normalize_platform_key(p: str) -> str:
        pk = (p or "").lower().strip()
        return "twitter" if pk == "x" else pk

    requested = [_normalize_platform_key(p) for p in (platforms or connected)]
    # Filter to requested + connected intersection (preserve order, dedupe).
    seen: set[str] = set()
    targets: list[str] = []
    for p in requested:
        if p in connected and p not in seen:
            targets.append(p)
            seen.add(p)

    # Evaluation / demo safety: if nothing would dispatch, avoid returning an
    # empty success that looks like a silent failure—explain explicitly. No
    # Celery jobs and no external APIs are invoked on this path.
    if not targets and settings.EVALUATION_MODE:
        logger.info(
            "master_publish.evaluation_dry_run",
            extra={"user_id": user_id, "content_id": content_id, "connected": connected},
        )
        return {
            "dispatched_to": [],
            "errors": [],
            "evaluation_mode": True,
            "dry_run": True,
            "message": (
                "Evaluation mode: no connected platforms matched this publish request. "
                "No jobs were queued and no external APIs were called. "
                "Connect accounts under Connections to run a live publish."
            ),
        }

    logger.info(
        "master_publish.start",
        extra={"user_id": user_id, "content_id": content_id, "platforms": targets},
    )

    async def _dispatch(platform: str) -> dict[str, Any]:
        p = (platform or "").lower()
        # Normalize "x" -> "twitter" so the user can request either alias.
        if p == "x":
            p = "twitter"

        # Dispatch through Celery (or eager local execution) for consistent retries,
        # timeouts, structured logging, and per-platform publishers.
        pv: PlatformVariant | None = None
        for v in (item.platform_variants or []):
            vp = (v.platform or "").lower()
            if vp == "x":
                vp = "twitter"
            if vp == p:
                pv = v
                break
        if pv is None:
            return {"ok": False, "error": "variant_missing", "message": "No platform variant for this target."}

        ar = publish_content_task.delay(str(pv.id))
        task_id = getattr(ar, "id", None)
        variant_id_str = str(pv.id)
        eager = bool(settings.CELERY_TASK_ALWAYS_EAGER or settings.CELERY_ALWAYS_EAGER)
        if not eager:
            return {"ok": True, "queued": True, "task_id": task_id, "platform_variant_id": variant_id_str}

        try:
            out = await asyncio.to_thread(ar.get, _EAGER_PUBLISH_GET_TIMEOUT_SEC)
        except BaseException as e:
            return {"ok": False, "error": type(e).__name__, "message": str(e)}

        if isinstance(out, dict):
            return dict(out)

        return {"ok": False, "error": "unexpected_result", "message": "Publish worker returned unexpected data."}

    tasks = [asyncio.create_task(_dispatch(p)) for p in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    dispatched_to: list[str] = []
    errors: list[dict[str, Any]] = []
    for platform, result in zip(targets, results, strict=False):
        if isinstance(result, BaseException):
            errors.append({"platform": platform, "error": type(result).__name__, "detail": str(result)})
            continue
        if not isinstance(result, dict):
            errors.append({"platform": platform, "error": "invalid_response"})
            continue
        ok = bool(result.get("ok"))
        detail = str(result.get("message") or result.get("error_message") or "").strip()
        if ok:
            dispatched_to.append(platform)
        elif result.get("status") == "blocked":
            errs: dict[str, Any] = {
                "platform": platform,
                "error": str(result.get("reason") or result.get("error") or "blocked"),
            }
            if detail:
                errs["detail"] = detail
            errors.append(errs)
        else:
            errs = {"platform": platform, "error": str(result.get("error") or "unknown")}
            if detail:
                errs["detail"] = detail
            errors.append(errs)

    logger.info(
        "master_publish.summary",
        extra={
            "user_id": user_id,
            "content_id": content_id,
            "dispatched_to": dispatched_to,
            "errors": errors,
        },
    )

    return {"dispatched_to": dispatched_to, "errors": errors}

