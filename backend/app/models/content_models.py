from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContentItemCreate(BaseModel):
    title: str | None = Field(default=None, max_length=500)


class ContentItemUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None


class PlatformVariantUpdateRequest(BaseModel):
    """Payload for PATCH .../variants/{platform} inline edits before publish."""

    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] = Field(default_factory=dict)


class PlatformVariantUpdateAck(BaseModel):
    status: str = "updated"
    platform: str
    content_id: str


class PlatformVariantResponse(BaseModel):
    id: UUID
    platform: str | None = None
    caption: str | None = None
    hashtags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    media_url: str | None = None
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    status: str
    error_message: str | None = None
    retry_count: int
    manually_edited: bool = False
    updated_at: datetime | None = None


class ContentItemResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str | None = None
    original_file_url: str | None = None
    file_type: str | None = None
    file_size_mb: float | None = None
    status: str
    scheduled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    platform_variants: list[PlatformVariantResponse] = []

