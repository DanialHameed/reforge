from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/analytics")

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.auth_models import User
from app.models.content_orm import ContentItem, PlatformVariant


@router.get("/overview")
async def overview():
    # TODO: implement analytics aggregation
    return {"overview": {}}


@router.get("/summary")
async def summary(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Analytics summary for the last N days (default 30).
    Aggregates published platform variants for the authenticated user.
    """

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    # Base constraint: variants belonging to this user's content, and with a publish time in range.
    base = (
        select(PlatformVariant, ContentItem)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
    )

    # total_published
    total_published = int(
        (
            await db.execute(
                select(func.count(PlatformVariant.id))
                .select_from(PlatformVariant)
                .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
                .where(
                    ContentItem.user_id == user.id,
                    PlatformVariant.status == "published",
                    PlatformVariant.published_at.is_not(None),
                    PlatformVariant.published_at >= start,
                    PlatformVariant.published_at <= now,
                )
            )
        ).scalar_one()
        or 0
    )

    # published_by_platform
    by_platform_rows = await db.execute(
        select(func.lower(PlatformVariant.platform), func.count(PlatformVariant.id))
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(func.lower(PlatformVariant.platform))
    )
    published_by_platform: dict[str, int] = {
        (p or "unknown"): int(c) for (p, c) in by_platform_rows.all()
    }

    # published_by_day (last N days)
    # Normalize to date in DB time zone; for SQLite this is fine as ISO.
    day_expr = func.date(PlatformVariant.published_at)
    by_day_rows = await db.execute(
        select(day_expr, func.count(PlatformVariant.id))
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(day_expr)
        .order_by(day_expr.asc())
    )

    by_day_map: dict[str, int] = {str(d): int(c) for (d, c) in by_day_rows.all()}
    published_by_day: list[dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        d: date = (now.date() - timedelta(days=i))
        key = d.isoformat()
        published_by_day.append({"date": key, "count": by_day_map.get(key, 0)})

    # success_rate_by_platform
    # Define success as published; failure as failed. Ignore other statuses for rate.
    success_rows = await db.execute(
        select(
            func.lower(PlatformVariant.platform).label("platform"),
            func.sum(case((PlatformVariant.status == "published", 1), else_=0)).label("published"),
            func.sum(case((PlatformVariant.status == "failed", 1), else_=0)).label("failed"),
        )
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
            PlatformVariant.status.in_(["published", "failed"]),
        )
        .group_by(func.lower(PlatformVariant.platform))
    )

    success_rate_by_platform: dict[str, float] = {}
    for p, pub, fail in success_rows.all():
        pub_i = int(pub or 0)
        fail_i = int(fail or 0)
        denom = max(1, pub_i + fail_i)
        success_rate_by_platform[p or "unknown"] = round((pub_i / denom) * 100.0, 2)

    # top_content: top 5 content items by published variant count
    top_rows = await db.execute(
        select(ContentItem.id, ContentItem.title, func.count(PlatformVariant.id).label("published_count"))
        .select_from(ContentItem)
        .join(PlatformVariant, PlatformVariant.content_item_id == ContentItem.id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(ContentItem.id, ContentItem.title)
        .order_by(func.count(PlatformVariant.id).desc())
        .limit(5)
    )
    top_content = [
        {"id": str(cid), "title": title, "published_count": int(cnt or 0)}
        for (cid, title, cnt) in top_rows.all()
    ]

    # Extra for frontend heatmap + line chart per platform: counts by day+platform + weekday/hour grid.
    by_day_platform_rows = await db.execute(
        select(
            func.date(PlatformVariant.published_at).label("date"),
            func.lower(PlatformVariant.platform).label("platform"),
            func.count(PlatformVariant.id).label("count"),
        )
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(func.date(PlatformVariant.published_at), func.lower(PlatformVariant.platform))
        .order_by(func.date(PlatformVariant.published_at).asc())
    )
    published_by_day_platform = [
        {"date": str(d), "platform": p or "unknown", "count": int(c)}
        for (d, p, c) in by_day_platform_rows.all()
    ]

    weekday_expr = func.strftime("%w", PlatformVariant.published_at)  # SQLite: 0=Sun..6=Sat
    hour_expr = func.strftime("%H", PlatformVariant.published_at)  # 00..23
    heat_rows = await db.execute(
        select(weekday_expr, hour_expr, func.count(PlatformVariant.id))
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(weekday_expr, hour_expr)
    )
    published_heatmap = [
        {"weekday": int(w), "hour": int(h), "count": int(c)}
        for (w, h, c) in heat_rows.all()
    ]

    # Content type breakdown: by content file_type and platform
    type_rows = await db.execute(
        select(
            func.lower(PlatformVariant.platform).label("platform"),
            func.lower(ContentItem.file_type).label("file_type"),
            func.count(PlatformVariant.id).label("count"),
        )
        .select_from(PlatformVariant)
        .join(ContentItem, ContentItem.id == PlatformVariant.content_item_id)
        .where(
            ContentItem.user_id == user.id,
            PlatformVariant.status == "published",
            PlatformVariant.published_at.is_not(None),
            PlatformVariant.published_at >= start,
            PlatformVariant.published_at <= now,
        )
        .group_by(func.lower(PlatformVariant.platform), func.lower(ContentItem.file_type))
    )
    content_type_breakdown = [
        {"platform": p or "unknown", "content_type": (t or "article"), "count": int(c)}
        for (p, t, c) in type_rows.all()
    ]

    return {
        "total_published": total_published,
        "published_by_platform": published_by_platform,
        "published_by_day": published_by_day,
        "success_rate_by_platform": success_rate_by_platform,
        "top_content": top_content,
        # extra fields for richer dashboard
        "published_by_day_platform": published_by_day_platform,
        "content_type_breakdown": content_type_breakdown,
        "published_heatmap": published_heatmap,
        "range": {"days": days, "start": start.isoformat(), "end": now.isoformat()},
    }

