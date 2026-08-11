from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content_orm import ContentItem


class ContentRepository:
    """Content item lookups scoped to owning user."""

    async def get_by_id_and_user(
        self,
        db: AsyncSession,
        *,
        content_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ContentItem | None:
        stmt = select(ContentItem).where(ContentItem.id == content_id, ContentItem.user_id == user_id)
        return (await db.execute(stmt)).scalar_one_or_none()


content_repository = ContentRepository()
