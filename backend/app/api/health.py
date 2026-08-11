from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.circuit_breaker import get_gemini_circuit_breaker
from app.core.config import settings
from app.core.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/live")
async def live() -> dict[str, Any]:
    return {"status": "ok", "environment": "windows_11"}


async def _redis_status() -> tuple[str, str | None]:
    """Best-effort Redis ping. Returns (status, error_message_or_none).

    Checks the configured broker URL (the one Celery actually uses). When no
    Redis is configured (local in-memory broker), reports ``"not_configured"``
    rather than failing the readiness probe — local dev should still pass.
    """
    broker_url = (settings.CELERY_BROKER_URL or "").strip()
    if not broker_url or broker_url.startswith(("memory:", "cache+memory:")):
        return "not_configured", None
    try:
        # Local import: redis is already a dependency (celery requires it),
        # but importing lazily keeps the route cheap when Redis is not used.
        import redis.asyncio as aioredis

        client = aioredis.from_url(broker_url, socket_timeout=2.0, socket_connect_timeout=2.0)
        try:
            pong = await client.ping()
        finally:
            await client.aclose()
        return ("ok" if pong else "degraded"), None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "health.redis_check_failed",
            extra={"broker_url_scheme": broker_url.split("://", 1)[0], "exc_type": type(exc).__name__},
        )
        return "down", f"{type(exc).__name__}: {exc}"


@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    # DB connectivity: async-safe SELECT 1.
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not ready",
        ) from exc

    redis_state, redis_err = await _redis_status()

    breaker = get_gemini_circuit_breaker()
    is_open = not breaker.is_available()

    # P-7 fix: Redis is a hard dependency in production (Celery broker). A
    # down Redis must surface as 503 so orchestrators can stop routing
    # traffic until the broker recovers. In local dev (memory broker) the
    # state is "not_configured" and we never fail the probe.
    if redis_state == "down":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "redis_down", "redis_error": redis_err},
        )

    overall = "ready"
    if is_open or redis_state == "degraded":
        overall = "degraded"

    payload: dict[str, Any] = {
        "status": overall,
        "database": "ok",
        "redis": redis_state,
        "gemini_circuit": ("open" if is_open else "closed"),
    }
    return payload


@router.get("/system")
async def system_health() -> dict[str, Any]:
    """
    Local system health metrics. Uses psutil; kept async-friendly for Windows.
    """

    def _read_metrics() -> dict[str, Any]:
        cpu_pct = float(psutil.cpu_percent(interval=0.1))
        vm = psutil.virtual_memory()
        root = Path(__file__).resolve().anchor or str(Path(__file__).resolve())
        du = psutil.disk_usage(root)
        return {
            "cpu": {"percent": cpu_pct},
            "memory": {
                "total_bytes": int(vm.total),
                "available_bytes": int(vm.available),
                "percent": float(vm.percent),
            },
            "disk": {
                "path": root,
                "total_bytes": int(du.total),
                "free_bytes": int(du.free),
                "used_bytes": int(du.used),
                "percent": float(du.percent),
            },
        }

    return await asyncio.to_thread(_read_metrics)

