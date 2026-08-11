"""B-2: generic publish path must set ``platform_variants.published_at``.

Native publishers (YouTube, Facebook, …) return early from ``_publish_variant``
after updating the row, including ``published_at``. The fallback path called
``publisher.publish(payload)`` then set ``published_at = None``, which made
``/api/v1/analytics/summary`` exclude every webhook-style publish because it
filters on ``published_at IS NOT NULL``.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem, PlatformVariant  # noqa: E402
from app.services.publishers.base import PublishResult, Publisher  # noqa: E402


class _DummyGenericPublisher(Publisher):
    """Not a native publisher class — ``_publish_variant`` uses the generic path."""

    provider = "dummy_generic"

    async def publish(self, payload: dict[str, Any]) -> PublishResult:
        return PublishResult(
            provider=self.provider,
            external_id="ext-1",
            url="https://example.com/post/1",
            raw=None,
        )


@pytest.mark.asyncio
async def test_generic_publish_path_sets_published_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.workers import publish_task

    db_path = tmp_path / "pub_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(publish_task, "SessionLocal", TestSession)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: _DummyGenericPublisher())

    uid = uuid.uuid4()
    cid = uuid.uuid4()
    pvid = uuid.uuid4()

    async with TestSession() as db:
        db.add(
            User(
                id=uid,
                email=f"{uid.hex[:8]}@example.com",
                hashed_password="x",
            )
        )
        db.add(
            ContentItem(
                id=cid,
                user_id=uid,
                title="t",
                status="draft",
                file_type="image",
            )
        )
        db.add(
            PlatformVariant(
                id=pvid,
                content_item_id=cid,
                platform="custom",
                caption="c",
                status="scheduled",
                media_url=None,
            )
        )
        await db.commit()

    out = await publish_task._publish_variant(str(pvid))
    assert out.get("ok") is True

    async with TestSession() as db:
        pv = (await db.execute(select(PlatformVariant).where(PlatformVariant.id == pvid))).scalar_one()
        assert pv.status == "published"
        assert pv.published_at is not None
        # SQLite round-trip may drop tzinfo; the worker still writes UTC-aware values.

    await engine.dispose()
