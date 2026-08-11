from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublishResult:
    provider: str
    external_id: str | None
    url: str | None
    raw: dict[str, Any] | None = None


class Publisher(ABC):
    provider: str

    @abstractmethod
    async def publish(self, payload: dict[str, Any]) -> PublishResult:
        raise NotImplementedError

