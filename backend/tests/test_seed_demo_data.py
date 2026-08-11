"""Demo data seeder must be idempotent and produce the expected demo state.

These tests pin two contracts that an evaluator depends on:

1.  Running ``scripts/seed_demo_data.py`` twice in a row must not create
    duplicate rows. Operators re-run the seed during demo prep (e.g.
    after a fresh ``alembic upgrade head``); the demo user, content
    items, variants, and activity log entries must stay deduplicated.

2.  The seeded dataset must include the analytics-relevant rows:
    at least one ``published`` variant with a non-null ``published_at``
    (or the ``/api/v1/analytics/summary`` charts will be empty, which
    looks broken on stage).

Hermetic: uses a per-test SQLite database and patches the seeder's
``SessionLocal`` so we never touch the dev DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base  # noqa: E402
from app.models.activity_orm import ActivityLog  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem, PlatformVariant  # noqa: E402
from scripts import seed_demo_data  # noqa: E402


@pytest_asyncio.fixture
async def isolated_seeder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "seed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(seed_demo_data, "SessionLocal", sessionmaker)

    try:
        yield sessionmaker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_creates_demo_user(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    rc = await seed_demo_data.main()
    assert rc == 0

    async with isolated_seeder() as db:
        user = (
            await db.execute(select(User).where(User.email == seed_demo_data.DEMO_EMAIL))
        ).scalar_one_or_none()
        assert user is not None
        assert user.email == "demo@reforge.local"
        assert user.hashed_password  # was actually hashed, not stored plain


@pytest.mark.asyncio
async def test_seed_creates_expected_content_count(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo_data.main()

    async with isolated_seeder() as db:
        n = (await db.execute(select(func.count()).select_from(ContentItem))).scalar_one()
    assert n == len(seed_demo_data.CONTENT_CATALOG)


@pytest.mark.asyncio
async def test_seed_is_idempotent_on_content(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        first = (
            await db.execute(select(func.count()).select_from(ContentItem))
        ).scalar_one()
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        second = (
            await db.execute(select(func.count()).select_from(ContentItem))
        ).scalar_one()
    assert first == second, "second seed run must not duplicate content rows"


@pytest.mark.asyncio
async def test_seed_is_idempotent_on_variants(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        first = (
            await db.execute(select(func.count()).select_from(PlatformVariant))
        ).scalar_one()
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        second = (
            await db.execute(select(func.count()).select_from(PlatformVariant))
        ).scalar_one()
    assert first == second, "second seed run must not duplicate variant rows"


@pytest.mark.asyncio
async def test_seed_is_idempotent_on_activity_log(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        first = (
            await db.execute(
                select(func.count())
                .select_from(ActivityLog)
                .where(ActivityLog.action == "demo_seeded")
            )
        ).scalar_one()
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        second = (
            await db.execute(
                select(func.count())
                .select_from(ActivityLog)
                .where(ActivityLog.action == "demo_seeded")
            )
        ).scalar_one()
    assert first == second


@pytest.mark.asyncio
async def test_seed_includes_published_variants_for_analytics(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    """``/api/v1/analytics/summary`` filters on ``published_at IS NOT NULL``.

    If the seeder ever stops producing such rows the analytics page
    looks broken on the demo, even though the system is working. Pin
    the contract here.
    """
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(PlatformVariant)
                .where(PlatformVariant.published_at.is_not(None))
            )
        ).scalar_one()
    assert n > 0, "seeder must create at least one published variant for analytics"


@pytest.mark.asyncio
async def test_seed_includes_scheduled_variants_for_queue(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    """The Queue page shows scheduled posts; the seed must include some."""
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(PlatformVariant)
                .where(PlatformVariant.status == "scheduled")
            )
        ).scalar_one()
    assert n > 0, "seeder must create scheduled variants for the queue page"


@pytest.mark.asyncio
async def test_seed_user_is_attached_to_all_content(
    isolated_seeder: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo_data.main()
    async with isolated_seeder() as db:
        user_id = (
            await db.execute(
                select(User.id).where(User.email == seed_demo_data.DEMO_EMAIL)
            )
        ).scalar_one()
        items = (
            await db.execute(select(ContentItem).where(ContentItem.user_id == user_id))
        ).scalars().all()
    assert len(items) == len(seed_demo_data.CONTENT_CATALOG)
