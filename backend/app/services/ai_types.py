from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ServiceUnavailableError(RuntimeError):
    pass


Mood = Literal["professional", "casual", "educational", "entertaining", "inspirational"]
ContentRating = Literal["general", "mature"]
AnalysisStatus = Literal["ok", "fallback"]


class MediaAnalysis(BaseModel):
    description: str = Field(min_length=1)
    main_topic: str = Field(min_length=1)
    themes: list[str] = Field(min_length=1)
    mood: Mood
    target_audience: str = Field(min_length=1)
    content_rating: ContentRating
    key_points: list[str] = Field(default_factory=list)
    analysis_status: AnalysisStatus = Field(
        default="ok",
        description="ok for normal Gemini output; fallback when analysis was degraded safely.",
    )


class PlatformContent(BaseModel):
    platform: str
    payload: dict[str, Any]

