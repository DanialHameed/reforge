from __future__ import annotations

from datetime import datetime, timezone

from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.content_orm import PlatformVariant
from app.workers.async_bridge import run_coroutine_for_celery
from app.workers.celery_app import celery_app
from app.workers.publish_task import publish_content_task

logger = get_task_logger(__name__)


async def _check_and_publish() -> dict:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        stmt = select(PlatformVariant).where(
            PlatformVariant.status == "scheduled",
            PlatformVariant.scheduled_at.is_not(None),
            PlatformVariant.scheduled_at <= now,
        )
        variants = (await db.execute(stmt)).scalars().all()

    enqueued = 0
    for pv in variants:
        publish_content_task.delay(str(pv.id))
        enqueued += 1

    return {"ok": True, "enqueued": enqueued}


@celery_app.task(name="reforge.check_and_publish")
def check_and_publish():
    logger.info("check_and_publish tick")
    return run_coroutine_for_celery(_check_and_publish(), timeout_sec=180)

