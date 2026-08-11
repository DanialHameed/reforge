from __future__ import annotations

from typing import Any

import httpx

from app.services.publishers.base import PublishResult, Publisher


class WebhookPublisher(Publisher):
    provider = "webhook"

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    async def publish(self, payload: dict[str, Any]) -> PublishResult:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(self._webhook_url, json=payload)
            r.raise_for_status()
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        return PublishResult(provider=self.provider, external_id=None, url=None, raw=data)

