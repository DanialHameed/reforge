"""
Run async coroutines from Celery sync tasks when a loop may already be running
(e.g. CELERY_TASK_ALWAYS_EAGER executing inside uvicorn's asyncio loop).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def run_coroutine_for_celery(coro: Any, *, timeout_sec: float = 3600) -> Any:
    """Run *coro* in an isolated event loop; offload to a worker thread if needed."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    out: list[Any] = []
    err: list[BaseException | None] = [None]
    evt = threading.Event()

    def runner() -> None:
        try:
            out.append(asyncio.run(coro))
        except BaseException as exc:
            err[0] = exc
        finally:
            evt.set()

    thread = threading.Thread(target=runner, name="reforge-celery-asyncio-bridge", daemon=True)
    thread.start()
    if not evt.wait(timeout=timeout_sec):
        logger.error("Coroutine bridge timed out after %ss", timeout_sec)
        raise TimeoutError("Background task timed out")
    if err[0] is not None:
        raise err[0]
    return out[0]
