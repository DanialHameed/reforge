"""
Gemini model fallback chain for ReForge (google.genai `Client.models.generate_content`).

Verified Gemini API Flash IDs: primary plus one reliable fallback.

History: ``gemini-1.5-flash`` was retired from the v1beta endpoint and now
returns 404, so the chain previously wasted ~15s of retries on a dead model
before falling through to the static fallback. Replaced with
``gemini-1.5-flash-latest`` (alias to the latest 1.5-flash GA build, still
served by v1beta) so the fallback path actually exercises a real model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeminiModelConfig:
    name: str
    rpm_limit: int
    supports_vision: bool
    timeout_seconds: float


GEMINI_MODEL_CHAIN: list[GeminiModelConfig] = [
    GeminiModelConfig(name="gemini-2.0-flash", rpm_limit=15, supports_vision=True, timeout_seconds=90.0),
    GeminiModelConfig(name="gemini-1.5-flash-latest", rpm_limit=15, supports_vision=True, timeout_seconds=60.0),
]
