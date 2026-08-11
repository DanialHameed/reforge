"""Regression: ``EVALUATION_MODE`` blocks destructive routes and enables publish dry-run."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, get_db  # noqa: E402
from app.core.security import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem, PlatformVariant  # noqa: E402


@pytest_asyncio.fixture
async def eval_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "EVALUATION_MODE", True)

    db_path = tmp_path / "eval.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user = User(
        id=uuid.uuid4(),
        email="eval@example.com",
        hashed_password="x",
    )
    item = ContentItem(
        id=uuid.uuid4(),
        user_id=user.id,
        title="t",
        status="draft",
    )
    async with sessionmaker() as s:
        s.add(user)
        s.add(item)
        await s.commit()

    async def _override_get_db():
        async with sessionmaker() as session:
            yield session

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, str(item.id)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_content_forbidden_in_evaluation_mode(eval_client):
    client, item_id = eval_client
    resp = await client.delete(f"/api/v1/content/{item_id}")
    assert resp.status_code == 403
    assert "Evaluation mode" in resp.text


@pytest.mark.asyncio
async def test_disconnect_platform_forbidden_in_evaluation_mode(eval_client):
    client, _ = eval_client
    resp = await client.delete("/api/v1/platforms/youtube")
    assert resp.status_code == 403
    assert "Evaluation mode" in resp.text


@pytest.mark.asyncio
async def test_platforms_status_includes_evaluation_flag(eval_client):
    client, _ = eval_client
    resp = await client.get("/api/v1/platforms/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("evaluation_mode") is True
    assert isinstance(body.get("platforms"), list)


@pytest.mark.asyncio
async def test_health_includes_evaluation_flag(eval_client):
    client, _ = eval_client
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json().get("evaluation_mode") is True


@pytest.mark.asyncio
async def test_publish_dry_run_when_no_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.core import config as config_module
    from app.services.master_publisher import publish_to_all_platforms

    monkeypatch.setattr(config_module.settings, "EVALUATION_MODE", True)

    db_path = tmp_path / "pub.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    uid = uuid.uuid4()
    cid = uuid.uuid4()
    user = User(id=uid, email="p@example.com", hashed_password="x")
    item = ContentItem(id=cid, user_id=uid, title="demo", status="draft")
    pv = PlatformVariant(
        id=uuid.uuid4(),
        content_item_id=cid,
        platform="instagram",
        caption="Real caption for demo that is not a generic fallback phrase.",
        hashtags=["tag"],
        status="draft",
    )
    async with sessionmaker() as s:
        s.add(user)
        s.add(item)
        s.add(pv)
        await s.commit()

    from app.core import database as database_module
    from app.services import master_publisher as mp

    monkeypatch.setattr(mp, "SessionLocal", sessionmaker)

    try:
        out = await publish_to_all_platforms(str(uid), str(cid), platforms=None)
    finally:
        await engine.dispose()

    assert out.get("dry_run") is True
    assert out.get("evaluation_mode") is True
    assert out.get("dispatched_to") == []
    assert out.get("errors") == []
    assert "Evaluation mode" in (out.get("message") or "")
