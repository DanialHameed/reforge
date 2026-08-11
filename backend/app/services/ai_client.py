from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AIResponse:
    text: str
    raw: dict[str, Any] | None = None


class ExternalAIClient:
    """
    Placeholder for external AI providers (no local models).
    Implement provider-specific clients (OpenAI/Anthropic/etc.) in this service layer.
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key

    async def ping(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(self._base_url)
            return r.status_code < 500

