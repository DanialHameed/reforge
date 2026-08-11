"""B-4 regression tests: ``AIService`` ghost reference in content_processor.

Before the fix, ``app/workers/content_processor.py`` referenced ``AIService``
in two helpers (``_analyze_only`` and ``_generate_after``) without importing
the symbol. The module loaded fine because Python resolves names lazily, but
the moment any code path called either helper the call site raised
``NameError: name 'AIService' is not defined``.

The live happy path in ``analyze_media_task`` does not use these helpers, so
the bug never showed up in production telemetry — it was a latent landmine
that would have detonated on any future change that wired the legacy two-step
result shape (``{"ok": True, ...}``) back into ``generate_variants_task``,
or on any direct invocation from a script, test, or the monolithic task.

These tests pin down:

1. The module exposes ``AIService`` and the symbol points at the real class
   defined in ``app.services.ai_service`` (no shadowing, no stub).
2. Calling ``_analyze_only`` against a missing content item returns the
   documented "not found" shape WITHOUT raising ``NameError`` at the
   ``ai_service = AIService()`` line.
3. The end-to-end success path of ``_analyze_only`` runs cleanly with a
   stubbed ``AIService``: it returns the legacy ``{"ok": True, ...}``
   shape and writes ``status='processing'`` to the DB before invoking AI —
   proving the ``AIService()`` construction site is now reachable.
4. Calling ``_generate_after`` on the legacy "ok" shape constructs an
   ``AIService`` (i.e. exercises the previously-broken site) without
   ``NameError``. We use a stubbed ``AIService`` so the test is hermetic.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem  # noqa: E402
from app.services.ai_service import AIService as RealAIService  # noqa: E402
from app.workers import content_processor  # noqa: E402


# ---------------------------------------------------------------------------
# Static guarantees about the import wiring
# ---------------------------------------------------------------------------


def test_aiservice_symbol_is_defined_in_module() -> None:
    """``content_processor.AIService`` must resolve to the real class.

    If the import is ever dropped again, this test fails with
    ``AttributeError`` immediately at collection time — long before any
    Celery worker has a chance to crash with ``NameError`` in production.
    """
    assert hasattr(content_processor, "AIService")
    assert content_processor.AIService is RealAIService


# ---------------------------------------------------------------------------
# Behavioral guarantees against the previously-broken call sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_only_returns_not_found_without_nameerror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-AI early-return branch must succeed without touching ``AIService``.

    The "content_item_not_found" branch returns before constructing
    ``AIService`` at all, so this test passes even if the constructor is
    expensive or requires a real API key. It guards the early-return
    contract while also proving the module-level import did not introduce
    any side-effect that breaks legacy callers.
    """
    db_path = tmp_path / "b4_notfound.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(content_processor, "SessionLocal", TestSession)

    out = await content_processor._analyze_only(
        str(uuid.uuid4()), str(uuid.uuid4())
    )
    assert out == {"ok": False, "error": "content_item_not_found"}

    await engine.dispose()


class _StubAIService:
    """Drop-in replacement for ``AIService`` used to keep tests hermetic."""

    def __init__(self) -> None:  # mirrors real signature
        self.analyze_calls: list[tuple[str, str]] = []

    def analyze_media(self, file_url: str, file_type: str):
        self.analyze_calls.append((file_url, file_type))
        from app.services.ai_types import MediaAnalysis

        return MediaAnalysis(
            description="stub description",
            main_topic="stub topic",
            themes=["stub-theme"],
            mood="casual",
            target_audience="stub audience",
            content_rating="general",
            key_points=["stub key point"],
            analysis_status="ok",
        )


@pytest.mark.asyncio
async def test_analyze_only_full_success_path_constructs_aiservice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: ``_analyze_only`` constructs ``AIService`` and returns the
    legacy ``{"ok": True, ...}`` shape.

    Before B-4 this test would fail with ``NameError`` at the
    ``ai_service = AIService()`` line. Now it returns the documented shape
    after invoking ``analyze_media`` exactly once.
    """
    db_path = tmp_path / "b4_success.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(content_processor, "SessionLocal", TestSession)
    monkeypatch.setattr(content_processor, "AIService", _StubAIService)

    user_id = uuid.uuid4()
    content_id = uuid.uuid4()
    file_url = "https://example.com/cat.jpg"
    async with TestSession() as db:
        db.add(
            User(
                id=user_id,
                email=f"{user_id.hex[:8]}@example.com",
                hashed_password="x",
            )
        )
        db.add(
            ContentItem(
                id=content_id,
                user_id=user_id,
                title="t",
                original_file_url=file_url,
                file_type="image",
                status="draft",
            )
        )
        await db.commit()

    out = await content_processor._analyze_only(str(content_id), str(user_id))

    assert out["ok"] is True
    assert out["content_item_id"] == str(content_id)
    assert out["user_id"] == str(user_id)
    assert out["detected_format"] in {"image", "video"}
    assert isinstance(out["analysis"], dict)
    assert out["analysis"]["description"] == "stub description"

    async with TestSession() as db:
        item = (
            await db.execute(select(ContentItem).where(ContentItem.id == content_id))
        ).scalar_one()
        assert item.status == "processing"

    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_after_short_circuits_on_failed_prev() -> None:
    """``_generate_after`` must short-circuit (returning ``prev``) when
    ``prev["ok"]`` is falsy — without ever touching ``AIService``.

    This guards the documented short-circuit contract that protected the
    pre-fix code from triggering the ``NameError`` in the failure path.
    """
    out = await content_processor._generate_after({"ok": False, "error": "x"})
    assert out == {"ok": False, "error": "x"}


@pytest.mark.asyncio
async def test_generate_after_invokes_aiservice_without_nameerror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The end-to-end success path of ``_generate_after`` reaches the
    ``ai_service = AIService()`` line that was previously a ``NameError``.

    We patch ``AIService.generate_platform_variants`` to a fast, deterministic
    coroutine so the test stays hermetic and does not call out to Gemini.
    """
    db_path = tmp_path / "b4_generate.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(content_processor, "SessionLocal", TestSession)

    user_id = uuid.uuid4()
    content_id = uuid.uuid4()
    async with TestSession() as db:
        db.add(
            User(
                id=user_id,
                email=f"{user_id.hex[:8]}@example.com",
                hashed_password="x",
            )
        )
        db.add(
            ContentItem(
                id=content_id,
                user_id=user_id,
                title="t",
                original_file_url="https://example.com/cat.jpg",
                file_type="image",
                status="processing",
            )
        )
        await db.commit()

    from app.core.fallbacks import get_all_platform_fallbacks
    from app.services.ai_types import PlatformContent

    fallbacks = get_all_platform_fallbacks()
    stub_variants = {
        "youtube": PlatformContent(platform="youtube", payload=fallbacks["youtube"]),
        "instagram": PlatformContent(platform="instagram", payload=fallbacks["instagram"]),
        "twitter": PlatformContent(platform="twitter", payload=fallbacks["twitter"]),
        "linkedin": PlatformContent(platform="linkedin", payload=fallbacks["linkedin"]),
        "facebook": PlatformContent(platform="facebook", payload=fallbacks["facebook"]),
    }

    class _StubFullAIService(_StubAIService):
        async def generate_platform_variants(self, analysis_obj, user_prefs=None):
            return stub_variants

    monkeypatch.setattr(content_processor, "AIService", _StubFullAIService)

    # Stub the media resize call so the test does not hit Cloudinary.
    class _StubResize:
        url = "https://example.com/resized.jpg"

    monkeypatch.setattr(
        content_processor.MediaService,
        "resize_for_platform",
        lambda self, url, platform, fmt: _StubResize(),
    )

    prev = {
        "ok": True,
        "content_item_id": str(content_id),
        "user_id": str(user_id),
        "detected_format": "image",
        "analysis": {
            "description": "stub",
            "main_topic": "stub",
            "themes": ["t"],
            "mood": "casual",
            "target_audience": "stub",
            "content_rating": "general",
            "key_points": [],
            "analysis_status": "ok",
        },
        "file_url": "https://example.com/cat.jpg",
    }

    out = await content_processor._generate_after(prev)

    assert out["ok"] is True
    assert isinstance(out["platform_variant_ids"], list)
    assert len(out["platform_variant_ids"]) == 5

    await engine.dispose()
