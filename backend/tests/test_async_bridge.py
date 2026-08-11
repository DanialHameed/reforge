"""Async bridge used when Celery eager runs tasks inside uvicorn's event loop."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.workers.async_bridge import run_coroutine_for_celery


def test_bridge_without_running_loop():
    async def doit():
        return 42

    assert run_coroutine_for_celery(doit(), timeout_sec=10) == 42


def test_bridge_when_loop_already_running():
    async def doit():
        return 99

    async def outer():
        return run_coroutine_for_celery(doit(), timeout_sec=30)

    assert asyncio.run(outer()) == 99
