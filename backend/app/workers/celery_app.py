from __future__ import annotations

"""
Celery application — Redis broker, production-hardened.
task_acks_late=True ensures tasks survive worker restarts.
task_reject_on_worker_lost=True re-queues tasks if worker dies mid-execution.
"""

from celery import Celery
from kombu import Exchange, Queue

from app.core.config import settings

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _create_celery() -> Celery:
    broker = str(settings.CELERY_BROKER_URL).strip()
    backend = str(settings.CELERY_RESULT_BACKEND).strip()
    # Explicit task registration is critical in production (avoids empty [tasks] list).
    c = Celery(
        "reforge",
        broker=broker,
        backend=backend,
        include=[
            "app.workers.content_processor",
            "app.workers.tasks",
            "app.workers.publisher_scheduler",
            "app.workers.publish_task",
        ],
    )
    c.conf.update(
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Reliability (CRITICAL — these prevent task loss on worker restart)
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # Result TTL
        result_expires=3600,
        # Queues / routing
        task_queues=(
            Queue("high_priority", Exchange("high_priority"), routing_key="high_priority"),
            Queue("default", Exchange("default"), routing_key="default"),
            Queue("low_priority", Exchange("low_priority"), routing_key="low_priority"),
        ),
        task_default_queue="default",
        task_default_exchange="default",
        task_default_routing_key="default",
        # Timeouts
        task_soft_time_limit=45,
        task_time_limit=60,
        # Retry behavior
        task_max_retries=2,
        task_default_retry_delay=3,
        # Redis connection / broker behavior
        broker_pool_limit=10,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=5,
        # Misc
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
    )
    # Local dev default: run tasks synchronously (no Redis/worker needed).
    # P-5 hardening: explicitly forbid eager mode in any non-local env, even
    # if the operator accidentally exported ``CELERY_TASK_ALWAYS_EAGER=true``.
    # ``app.main.lifespan`` already calls ``validate_production_runtime_at_startup``,
    # but Celery workers boot via ``celery -A app.workers.celery_app worker``
    # without going through ``app.main`` — they would otherwise still honor
    # the bad value. The belt-and-braces guard here means "eager in
    # production" cannot happen regardless of how the worker is started.
    env = (settings.ENV or "local").strip().lower()
    requested_eager = bool(settings.CELERY_TASK_ALWAYS_EAGER or settings.CELERY_ALWAYS_EAGER)
    if env == "local":
        # Preserve previous local behavior: ENV=local OR explicit eager flag.
        eager = True
    else:
        if requested_eager:
            import logging

            logging.getLogger("reforge.celery").error(
                "celery.eager_refused env=%s requested_eager=True — production "
                "requires a real broker; eager mode is being forced off.",
                env,
            )
        eager = False
    c.conf.task_always_eager = eager

    # Celery beat schedule: run publisher scheduler every 60 seconds.
    c.conf.beat_schedule = {
        "reforge-check-and-publish-every-60s": {
            "task": "reforge.check_and_publish",
            "schedule": 60.0,
        }
    }
    # Safety net for any future tasks added under app.workers.*
    c.autodiscover_tasks(["app.workers"])

    # Start worker: celery -A app.celery_app worker --loglevel=info --concurrency=4 --queues=high_priority,default,low_priority --max-tasks-per-child=100
    # Start beat:   celery -A app.celery_app beat --loglevel=info
    # Monitor:      celery -A app.celery_app flower --port=5555
    return c


celery_app = _create_celery()

