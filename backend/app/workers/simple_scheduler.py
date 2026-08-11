from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class SimpleScheduler:
    """
    Minimal in-process scheduler (no Redis/BullMQ).
    Suitable for lightweight periodic tasks; for heavier workloads prefer a DB-backed
    job table and a separate worker deployment.
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def every(self, seconds: float, fn: Callable[[], Awaitable[None]]) -> None:
        async def _runner() -> None:
            while True:
                try:
                    await fn()
                except Exception:
                    logger.exception("Scheduled task failed")
                await asyncio.sleep(seconds)

        self._tasks.append(asyncio.create_task(_runner()))

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

