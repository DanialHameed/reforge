"""
Idempotent demo data seeder for the ReForge evaluator demo.

Usage (from the ``backend/`` directory):

    python scripts/seed_demo_data.py

What it creates / refreshes (idempotent — safe to re-run):

    * A demo user:   demo@reforge.dev  /  ReForge!Demo123
    * 6 ContentItems with distinct titles, statuses, and timestamps so
      the dashboard, content list, and queue all have something to show.
    * Per-content PlatformVariants for instagram, twitter, linkedin,
      facebook, youtube — each with a real, non-fallback caption and
      hashtags so they pass the master-publisher placeholder gate.
    * A spread of ``status`` values across the variants so the queue
      shows drafts AND scheduled AND published items.
    * A handful of historical ``published`` variants spanning the last
      30 days so ``/api/v1/analytics/summary`` produces non-empty
      charts immediately.
    * A few ``ActivityLog`` rows for the recent-activity feed.

Design notes:

    * No OAuth tokens / no SocialAccount rows are created — those
      require a real OAuth handshake. The connections page will
      correctly show every platform as "not connected" and the
      evaluator can connect their own accounts at demo time.
    * Idempotency is achieved with ``ON CONFLICT``-style upserts in
      Python: we look up by stable natural keys (email for the user,
      ``(user_id, title)`` for content, ``(content_id, platform)`` for
      variants) and update-or-insert.
    * All timestamps are UTC.
    * Password hashing goes through the real ``hash_password`` so the
      seeded user can log in via the normal /api/v1/auth/login path.

Exit codes:
    0   success
    1   unrecoverable error (bad DB URL, table missing, etc.)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Make ``app.*`` importable when run as ``python scripts/seed_demo_data.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.activity_orm import ActivityLog  # noqa: E402
from app.models.auth_models import User  # noqa: E402
from app.models.content_orm import ContentItem, PlatformVariant  # noqa: E402

logger = logging.getLogger("reforge.seed")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


# NOT .local/.test/.example/.invalid — email-validator (used by the /auth/login
# EmailStr schema) unconditionally rejects those as IANA special-use domains,
# which made the seeded demo account unable to log in via the real API.
DEMO_EMAIL = "demo@reforge.dev"
DEMO_PASSWORD = "ReForge!Demo123"
DEMO_DISPLAY_NAME = "Demo Account"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Static catalog of seeded content. Each entry produces one ContentItem and
# one PlatformVariant per platform listed under ``variants``.
# ---------------------------------------------------------------------------


CONTENT_CATALOG: list[dict[str, Any]] = [
    {
        "title": "Launch Day — Product Demo Reel",
        "status": "published",
        "scheduled_offset_days": -7,
        "file_type": "video",
        "media_url": "https://res.cloudinary.com/demo/video/upload/v1/sample.mp4",
        "variants": [
            {
                "platform": "instagram",
                "status": "published",
                "published_offset_days": -7,
                "caption": "Day one is here. Watch what we built and why it matters for creators shipping every week.",
                "hashtags": ["productlaunch", "creators", "behindthescenes"],
            },
            {
                "platform": "twitter",
                "status": "published",
                "published_offset_days": -7,
                "caption": "Launch day. Six months of work compressed into a 60-second reel. Watch the demo and tell us what to build next.",
                "hashtags": ["launchday", "buildinpublic"],
            },
            {
                "platform": "linkedin",
                "status": "published",
                "published_offset_days": -7,
                "caption": "Today we shipped. A walkthrough of how teams turn one upload into platform-ready posts in under five minutes.",
                "hashtags": ["productlaunch", "automation"],
            },
            {
                "platform": "youtube",
                "status": "published",
                "published_offset_days": -7,
                "caption": "ReForge — Launch Day Demo",
                "hashtags": ["product", "demo"],
                "metadata": {
                    "title": "ReForge — Launch Day Demo",
                    "description": "A two-minute walkthrough of how a single upload becomes Instagram, Twitter, LinkedIn, Facebook, and YouTube posts.",
                    "tags": ["product", "demo", "launch"],
                    "privacy": "public",
                },
            },
            {
                "platform": "facebook",
                "status": "published",
                "published_offset_days": -7,
                "caption": "We launched today. Here's a quick look at what ReForge does and how teams are already using it.",
                "hashtags": ["launch", "teams"],
            },
        ],
    },
    {
        "title": "Behind The Build — Engineering Diary #04",
        "status": "scheduled",
        "scheduled_offset_days": 2,
        "file_type": "image",
        "media_url": "https://res.cloudinary.com/demo/image/upload/v1/sample.jpg",
        "variants": [
            {
                "platform": "instagram",
                "status": "scheduled",
                "scheduled_offset_days": 2,
                "caption": "Engineering diary #04 — what we learned shipping a queue that survives 5x traffic spikes.",
                "hashtags": ["engineering", "scaling", "buildinpublic"],
            },
            {
                "platform": "twitter",
                "status": "scheduled",
                "scheduled_offset_days": 2,
                "caption": "Engineering diary #04 → how we cut p99 latency from 1.8s to 240ms by moving exactly one query out of the request path.",
                "hashtags": ["engineering", "performance"],
            },
            {
                "platform": "linkedin",
                "status": "scheduled",
                "scheduled_offset_days": 2,
                "caption": "A short engineering write-up on cutting p99 latency by 7x with one targeted refactor. The change touched 12 lines.",
                "hashtags": ["engineering", "performance"],
            },
        ],
    },
    {
        "title": "Customer Story — How Helio Saved 6 Hours/Week",
        "status": "scheduled",
        "scheduled_offset_days": 4,
        "file_type": "video",
        "media_url": "https://res.cloudinary.com/demo/video/upload/v1/sample.mp4",
        "variants": [
            {
                "platform": "instagram",
                "status": "scheduled",
                "scheduled_offset_days": 4,
                "caption": "How a four-person creator team turned 6 hours of weekly posting work into a 25-minute review session.",
                "hashtags": ["customerstory", "creators", "automation"],
            },
            {
                "platform": "linkedin",
                "status": "scheduled",
                "scheduled_offset_days": 4,
                "caption": "A small content team replaced their cross-platform publishing workflow with one upload + a five-minute review. Here is the breakdown.",
                "hashtags": ["customerstory", "productivity"],
            },
            {
                "platform": "youtube",
                "status": "scheduled",
                "scheduled_offset_days": 4,
                "caption": "Helio Customer Story — Saved 6 Hours/Week with ReForge",
                "hashtags": ["customer", "story"],
                "metadata": {
                    "title": "Helio Customer Story — Saved 6 Hours/Week with ReForge",
                    "description": "Helio's content team explains how a single upload became posts on five platforms, with review workflows still in their hands.",
                    "tags": ["customer", "story", "automation"],
                    "privacy": "public",
                },
            },
        ],
    },
    {
        "title": "Tip of the Week — Caption Hooks That Convert",
        "status": "draft",
        "scheduled_offset_days": None,
        "file_type": "image",
        "media_url": "https://res.cloudinary.com/demo/image/upload/v1/sample.jpg",
        "variants": [
            {
                "platform": "instagram",
                "status": "draft",
                "caption": "Three caption hooks that lifted our save rate by 31% this month. Steal them.",
                "hashtags": ["tipoftheweek", "marketing", "copywriting"],
            },
            {
                "platform": "twitter",
                "status": "draft",
                "caption": "Three caption hooks that lifted save rate 31% this month. Stealable.",
                "hashtags": ["copywriting", "growth"],
            },
        ],
    },
    {
        "title": "Year-End Recap — 2025 Highlight Reel",
        "status": "published",
        "scheduled_offset_days": -22,
        "file_type": "video",
        "media_url": "https://res.cloudinary.com/demo/video/upload/v1/sample.mp4",
        "variants": [
            {
                "platform": "instagram",
                "status": "published",
                "published_offset_days": -22,
                "caption": "Twelve months in 90 seconds. Thanks for shipping with us.",
                "hashtags": ["yearinreview", "2025", "creators"],
            },
            {
                "platform": "youtube",
                "status": "published",
                "published_offset_days": -22,
                "caption": "ReForge — 2025 in 90 Seconds",
                "hashtags": ["recap", "2025"],
                "metadata": {
                    "title": "ReForge — 2025 in 90 Seconds",
                    "description": "Highlights from a year of shipping: launches, integrations, and the customer wins that powered everything.",
                    "tags": ["recap", "2025", "year-in-review"],
                    "privacy": "public",
                },
            },
            {
                "platform": "linkedin",
                "status": "published",
                "published_offset_days": -22,
                "caption": "Year-end recap: what we shipped, what we learned, and where we're aimed in Q1.",
                "hashtags": ["recap", "team"],
            },
        ],
    },
    {
        "title": "Feature Walkthrough — Smart Hashtag Generator",
        "status": "published",
        "scheduled_offset_days": -3,
        "file_type": "video",
        "media_url": "https://res.cloudinary.com/demo/video/upload/v1/sample.mp4",
        "variants": [
            {
                "platform": "instagram",
                "status": "published",
                "published_offset_days": -3,
                "caption": "We trained a small model on what actually performs in your niche. 30-second walkthrough inside.",
                "hashtags": ["feature", "ai", "creators"],
            },
            {
                "platform": "twitter",
                "status": "published",
                "published_offset_days": -3,
                "caption": "New: hashtag generator that learns from what actually works for your account, not what is trending in general.",
                "hashtags": ["product", "ai"],
            },
            {
                "platform": "linkedin",
                "status": "published",
                "published_offset_days": -3,
                "caption": "Generic hashtag suggestions waste impressions. The new generator measures per-account performance and adapts in days, not months.",
                "hashtags": ["product", "ai"],
            },
            {
                "platform": "facebook",
                "status": "published",
                "published_offset_days": -3,
                "caption": "Smart hashtag generator is live. Walkthrough video in comments.",
                "hashtags": ["feature"],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Upsert helpers.
# ---------------------------------------------------------------------------


async def _get_or_create_user(db: AsyncSession) -> User:
    user = (
        await db.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if user is not None:
        # Re-hash the password every run so a forgotten demo password is
        # always recoverable by re-running the seed.
        user.hashed_password = hash_password(DEMO_PASSWORD)
        user.display_name = DEMO_DISPLAY_NAME
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("demo user updated: %s", user.email)
        return user

    user = User(
        id=uuid.uuid4(),
        email=DEMO_EMAIL,
        hashed_password=hash_password(DEMO_PASSWORD),
        display_name=DEMO_DISPLAY_NAME,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("demo user created: %s", user.email)
    return user


def _resolve_scheduled_at(spec: dict[str, Any], now: datetime) -> datetime | None:
    offset = spec.get("scheduled_offset_days")
    if offset is None:
        return None
    return now + timedelta(days=int(offset))


def _resolve_published_at(spec: dict[str, Any], now: datetime) -> datetime | None:
    offset = spec.get("published_offset_days")
    if offset is None:
        return None
    return now + timedelta(days=int(offset))


async def _upsert_content_with_variants(
    db: AsyncSession, user: User, spec: dict[str, Any], now: datetime
) -> tuple[ContentItem, int, int]:
    """Upsert a ContentItem keyed on (user_id, title) plus its variants.

    Returns ``(content_item, variants_inserted, variants_updated)``.
    """
    title = spec["title"]
    item = (
        await db.execute(
            select(ContentItem).where(
                ContentItem.user_id == user.id,
                ContentItem.title == title,
            )
        )
    ).scalar_one_or_none()

    scheduled_at = _resolve_scheduled_at(spec, now)
    if item is None:
        item = ContentItem(
            id=uuid.uuid4(),
            user_id=user.id,
            title=title,
            status=spec.get("status", "draft"),
            file_type=spec.get("file_type"),
            original_file_url=spec.get("media_url"),
            scheduled_at=scheduled_at,
        )
        db.add(item)
    else:
        item.status = spec.get("status", item.status)
        item.file_type = spec.get("file_type", item.file_type)
        item.original_file_url = spec.get("media_url", item.original_file_url)
        item.scheduled_at = scheduled_at
        db.add(item)
    await db.commit()
    await db.refresh(item)

    inserted = 0
    updated = 0
    for v_spec in spec.get("variants", []):
        platform = str(v_spec["platform"]).lower()
        existing = (
            await db.execute(
                select(PlatformVariant).where(
                    PlatformVariant.content_item_id == item.id,
                    PlatformVariant.platform == platform,
                )
            )
        ).scalar_one_or_none()

        v_scheduled = _resolve_scheduled_at(v_spec, now)
        v_published = _resolve_published_at(v_spec, now)

        if existing is None:
            existing = PlatformVariant(
                id=uuid.uuid4(),
                content_item_id=item.id,
                platform=platform,
            )
            inserted += 1
        else:
            updated += 1

        existing.caption = v_spec.get("caption") or existing.caption
        existing.hashtags = v_spec.get("hashtags") or existing.hashtags
        existing.metadata_json = v_spec.get("metadata") or existing.metadata_json
        existing.media_url = spec.get("media_url") or existing.media_url
        existing.status = v_spec.get("status", existing.status or "draft")
        existing.scheduled_at = v_scheduled
        existing.published_at = v_published
        existing.error_message = None
        db.add(existing)

    await db.commit()
    return item, inserted, updated


async def _ensure_activity_log(
    db: AsyncSession, user: User, items: list[ContentItem]
) -> int:
    """Add a single recent-activity row per published item if none exists.

    Idempotent via a lookup on ``(user_id, content_item_id, action)``.
    """
    inserted = 0
    for item in items:
        already = (
            await db.execute(
                select(ActivityLog).where(
                    ActivityLog.user_id == user.id,
                    ActivityLog.content_item_id == item.id,
                    ActivityLog.action == "demo_seeded",
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            continue
        db.add(
            ActivityLog(
                user_id=user.id,
                content_item_id=item.id,
                action="demo_seeded",
                details={"source": "scripts/seed_demo_data.py"},
            )
        )
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


async def main() -> int:
    now = _utcnow()
    async with SessionLocal() as db:
        user = await _get_or_create_user(db)

        total_inserted = 0
        total_updated = 0
        seeded_items: list[ContentItem] = []
        for spec in CONTENT_CATALOG:
            item, inserted, updated = await _upsert_content_with_variants(
                db, user, spec, now
            )
            seeded_items.append(item)
            total_inserted += inserted
            total_updated += updated
            logger.info(
                "content seeded title=%r status=%s variants_inserted=%d variants_updated=%d",
                item.title,
                item.status,
                inserted,
                updated,
            )

        activities = await _ensure_activity_log(db, user, seeded_items)

    logger.info("=" * 60)
    logger.info("Demo seed complete.")
    logger.info("  user:                 %s", DEMO_EMAIL)
    logger.info("  password:             %s", DEMO_PASSWORD)
    logger.info("  content items:        %d", len(seeded_items))
    logger.info("  variants inserted:    %d", total_inserted)
    logger.info("  variants updated:     %d", total_updated)
    logger.info("  activity rows added:  %d", activities)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
