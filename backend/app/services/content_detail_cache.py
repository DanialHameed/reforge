"""Short-TTL cache for GET /content/{id} responses to lower DB + serialization load."""

from __future__ import annotations

import json
from typing import Any

from cachetools import TTLCache

_ttl = TTLCache(maxsize=1024, ttl=10)

_TRANSIENT_NO_CACHE = frozenset({"processing"})


def detail_cache_key(user_id: str, content_id: str) -> str:
    return f"content_detail:{user_id}:{content_id}"


def get_cached(user_id: str, content_id: str) -> dict[str, Any] | None:
    k = detail_cache_key(user_id, content_id)
    v = _ttl.get(k)
    if v is None:
        return None
    try:
        d = json.loads(v)
    except json.JSONDecodeError:
        return None
    # In-flight pipelines mutate rows; stale cached "processing" blocks fresh polls.
    if isinstance(d, dict) and str(d.get("status", "")).lower() in _TRANSIENT_NO_CACHE:
        _ttl.pop(k, None)
        return None
    return d


def set_cached(user_id: str, content_id: str, payload: dict[str, Any]) -> None:
    if str(payload.get("status", "")).lower() in _TRANSIENT_NO_CACHE:
        return
    k = detail_cache_key(user_id, content_id)
    _ttl[k] = json.dumps(payload, ensure_ascii=False, default=str)


def invalidate(user_id: str, content_id: str) -> None:
    _ttl.pop(detail_cache_key(user_id, content_id), None)
