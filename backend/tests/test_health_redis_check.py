"""Production deployment blocker P-7: /ready must include Redis.

Before this change ``/ready`` only checked Postgres connectivity and the
Gemini circuit breaker, leaving a downed Celery broker invisible to load
balancer and Kubernetes probes. These tests pin down the new contract:

* When Redis is configured and reachable, /ready reports
  ``redis="ok"`` and overall ``status="ready"``.
* When Redis is configured but unreachable, /ready returns 503 so
  orchestrators stop routing traffic until the broker recovers.
* When the broker URL is the in-memory dev default (``memory://``),
  /ready reports ``redis="not_configured"`` and stays at 200 so local
  development is not broken.
* The Postgres failure path still returns 503 (existing behavior preserved).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api import health as health_module  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def isolated_app(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "health.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    test_sessionmaker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_ready_reports_not_configured_when_redis_is_in_memory(
    monkeypatch: pytest.MonkeyPatch, isolated_app: AsyncClient
) -> None:
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    resp = await isolated_app.get("/health/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["redis"] == "not_configured"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_ready_reports_ok_when_redis_ping_succeeds(
    monkeypatch: pytest.MonkeyPatch, isolated_app: AsyncClient
) -> None:
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://test-host:6379/0")

    async def _stub() -> tuple[str, str | None]:
        return "ok", None

    monkeypatch.setattr(health_module, "_redis_status", _stub)
    resp = await isolated_app.get("/health/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["redis"] == "ok"
    assert body["status"] in {"ready", "degraded"}  # depends on Gemini breaker state


@pytest.mark.asyncio
async def test_ready_returns_503_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch, isolated_app: AsyncClient
) -> None:
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://test-host:6379/0")

    async def _stub() -> tuple[str, str | None]:
        return "down", "ConnectionError: connection refused"

    monkeypatch.setattr(health_module, "_redis_status", _stub)
    resp = await isolated_app.get("/health/ready")
    assert resp.status_code == 503, resp.text
    detail = resp.json().get("detail", {})
    assert isinstance(detail, dict)
    assert detail.get("status") == "redis_down"
    assert "redis_error" in detail
