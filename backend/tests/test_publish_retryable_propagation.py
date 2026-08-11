"""Production hardening P-6: ``_publish_variant`` must propagate RetryablePublishError.

Root cause of the regression these tests pin:

    Earlier ``_publish_variant`` wrapped the entire native-publisher
    routing block in a single ``except Exception`` handler. When a
    publisher raised ``RetryablePublishError`` (Twitter / Facebook
    rate-limit, HTTP 429), that handler:

        1. Caught the exception.
        2. Set ``pv.status = "failed"``.
        3. Returned ``{"ok": False, ...}``.

    Because the coroutine returned a value (no exception bubbled out),
    the outer ``publish_content_task`` Celery wrapper never entered its
    ``except RetryablePublishError`` branch and therefore never called
    ``self.retry(countdown=...)``. Every Twitter / Facebook 429 was
    silently promoted to a permanent failure even though the publisher
    explicitly asked for a retry.

    The same hole existed for the generic ``await publisher.publish(...)``
    path used by webhook publishers.

The fix:

    * ``RetryablePublishError`` is now caught BEFORE the broad
      ``except Exception`` and is re-raised after reverting the variant
      to its pre-publish status (``scheduled`` typically) so the next
      Celery attempt re-enters the publish flow cleanly.
    * ``retry_count`` is incremented and ``error_message`` records the
      backoff hint so operators can see the throttle timeline in the
      dashboard.
    * The same shape applies to the webhook fallback path.

These tests run an isolated in-memory style SQLite database and inject
fake publishers; they do not touch the real Twitter, Facebook, or
network stack.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem, PlatformVariant  # noqa: E402
from app.services.publishers.errors import RetryablePublishError  # noqa: E402
from app.services.publishers.twitter_publisher import TwitterPublisher  # noqa: E402
from app.workers import publish_task  # noqa: E402


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def isolated_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Spin up a fresh SQLite DB per test and patch publish_task.SessionLocal.

    We use ``Base.metadata.create_all`` (not Alembic) because this test is
    not exercising migration code; it is exercising publish-task control
    flow. Migration coverage lives in ``test_alembic_schema_drift.py``.
    """
    db_path = tmp_path / "publish_retryable.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # _publish_variant uses module-global SessionLocal; redirect it.
    monkeypatch.setattr(publish_task, "SessionLocal", sessionmaker)

    try:
        yield sessionmaker
    finally:
        await engine.dispose()


async def _make_variant(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    platform: str = "twitter",
    status: str = "scheduled",
    media_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert a User + ContentItem + PlatformVariant and return the variant id."""
    async with sessionmaker() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
        )
        db.add(user)
        await db.flush()

        item = ContentItem(
            id=uuid.uuid4(),
            user_id=user.id,
            title="t",
            status="ready",
        )
        db.add(item)
        await db.flush()

        pv = PlatformVariant(
            id=uuid.uuid4(),
            content_item_id=item.id,
            platform=platform,
            caption="hello",
            hashtags=[],
            metadata_json=metadata or {},
            media_url=media_url,
            status=status,
        )
        db.add(pv)
        await db.commit()
        return pv.id


async def _reload_variant(
    sessionmaker: async_sessionmaker[AsyncSession], pv_id: uuid.UUID
) -> PlatformVariant:
    async with sessionmaker() as db:
        pv = (
            await db.execute(select(PlatformVariant).where(PlatformVariant.id == pv_id))
        ).scalar_one()
        return pv


# ---------------------------------------------------------------------------
# Native-publisher path: TwitterPublisher.post_tweet raises RetryablePublishError
# ---------------------------------------------------------------------------


class _RateLimitedTwitter(TwitterPublisher):
    """Pretends to be the Twitter native publisher but always 429s."""

    def __init__(self, retry_after: int = 90) -> None:
        self._retry_after = retry_after
        self.calls = 0

    async def post_tweet(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:  # noqa: D401
        self.calls += 1
        raise RetryablePublishError(
            "Twitter rate limit (429): too many requests",
            retry_after_seconds=self._retry_after,
        )


@pytest.mark.asyncio
async def test_native_path_reraises_retryable_publish_error(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")

    fake = _RateLimitedTwitter(retry_after=90)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError) as excinfo:
        await publish_task._publish_variant(str(pv_id))

    assert excinfo.value.retry_after_seconds == 90
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_native_path_reverts_status_to_pre_publish_value(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")

    fake = _RateLimitedTwitter(retry_after=120)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError):
        await publish_task._publish_variant(str(pv_id))

    pv = await _reload_variant(isolated_db, pv_id)
    # Critical: must NOT be "failed" — that was the original swallowing bug.
    assert pv.status == "scheduled", (
        "RetryablePublishError must revert status, not mark as failed"
    )


@pytest.mark.asyncio
async def test_native_path_increments_retry_count(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")

    fake = _RateLimitedTwitter(retry_after=30)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError):
        await publish_task._publish_variant(str(pv_id))

    pv = await _reload_variant(isolated_db, pv_id)
    assert pv.retry_count == 1


@pytest.mark.asyncio
async def test_native_path_records_retry_after_in_error_message(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")

    fake = _RateLimitedTwitter(retry_after=600)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError):
        await publish_task._publish_variant(str(pv_id))

    pv = await _reload_variant(isolated_db, pv_id)
    assert pv.error_message is not None
    assert "retry_after=600s" in pv.error_message


@pytest.mark.asyncio
async def test_native_path_preserves_publishing_status_when_already_publishing(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variant manually re-queued while still ``publishing`` reverts cleanly."""
    pv_id = await _make_variant(isolated_db, platform="twitter", status="publishing")

    fake = _RateLimitedTwitter(retry_after=10)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError):
        await publish_task._publish_variant(str(pv_id))

    pv = await _reload_variant(isolated_db, pv_id)
    # We snapshot the status BEFORE the inner mark_publishing flip; if the
    # variant was already "publishing" we revert to that, not to "scheduled"
    # (we do not invent state we did not observe).
    assert pv.status == "publishing"


# ---------------------------------------------------------------------------
# Generic publisher path: publisher.publish(payload) raises RetryablePublishError
# ---------------------------------------------------------------------------


class _GenericRateLimitedPublisher:
    """Stand-in for a webhook-style publisher; not a known native class."""

    provider = "webhook"

    def __init__(self, retry_after: int = 45) -> None:
        self._retry_after = retry_after
        self.calls = 0

    async def publish(self, _payload: dict[str, Any]) -> Any:
        self.calls += 1
        raise RetryablePublishError(
            "Upstream webhook returned 429",
            retry_after_seconds=self._retry_after,
        )


@pytest.mark.asyncio
async def test_generic_path_reraises_retryable_publish_error(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="webhook", status="scheduled")

    fake = _GenericRateLimitedPublisher(retry_after=45)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError) as excinfo:
        await publish_task._publish_variant(str(pv_id))

    assert excinfo.value.retry_after_seconds == 45
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_generic_path_reverts_status(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="webhook", status="scheduled")

    fake = _GenericRateLimitedPublisher(retry_after=60)
    monkeypatch.setattr(publish_task, "get_publisher_for_platform", lambda _p: fake)

    with pytest.raises(RetryablePublishError):
        await publish_task._publish_variant(str(pv_id))

    pv = await _reload_variant(isolated_db, pv_id)
    assert pv.status == "scheduled"
    assert pv.retry_count == 1


# ---------------------------------------------------------------------------
# Negative path: terminal exceptions must still mark the variant failed
# ---------------------------------------------------------------------------


class _BrokenTwitter(TwitterPublisher):
    """Raises a non-retryable error to verify the failure path still works."""

    async def post_tweet(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        raise ValueError("invalid payload, do not retry")


@pytest.mark.asyncio
async def test_terminal_exception_marks_variant_failed_and_returns_dict(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")
    monkeypatch.setattr(
        publish_task, "get_publisher_for_platform", lambda _p: _BrokenTwitter()
    )

    result = await publish_task._publish_variant(str(pv_id))

    # Terminal errors are returned as dicts (no Celery retry).
    assert result["ok"] is False
    assert result["error"] == "publish_failed"

    pv = await _reload_variant(isolated_db, pv_id)
    assert pv.status == "failed"
    assert pv.error_message is not None
    assert "invalid payload" in pv.error_message


# ---------------------------------------------------------------------------
# Happy path smoke: native success still records published_at
# ---------------------------------------------------------------------------


class _SuccessfulTwitter(TwitterPublisher):
    async def post_tweet(self, variant_id: str, db: AsyncSession) -> dict[str, Any]:
        # Mimic what the real native publisher does: mark published itself.
        pv = (
            await db.execute(
                select(PlatformVariant).where(PlatformVariant.id == uuid.UUID(variant_id))
            )
        ).scalar_one()
        pv.status = "published"
        pv.published_at = datetime.now(timezone.utc)
        pv.error_message = None
        db.add(pv)
        await db.commit()
        return {"ok": True, "platform_variant_id": variant_id}


@pytest.mark.asyncio
async def test_native_success_does_not_get_treated_as_failure(
    isolated_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    pv_id = await _make_variant(isolated_db, platform="twitter", status="scheduled")
    monkeypatch.setattr(
        publish_task, "get_publisher_for_platform", lambda _p: _SuccessfulTwitter()
    )

    result = await publish_task._publish_variant(str(pv_id))
    assert result["ok"] is True

    pv = await _reload_variant(isolated_db, pv_id)
    assert pv.status == "published"
    assert pv.published_at is not None
