"""
Analysis + Gemini infra cache:

- Analysis JSON: memory TTL + optional Redis (`image_analysis:{sha256}`)
- Distributed lock across workers: `lock:image_analysis:{sha256}` (SET NX + safe release)
- Cached Gemini uploaded file refs: `gemini_file:{...}` (24h TTL)
- Optional Redis: global rate-limit window + shared circuit-breaker expiry
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import random
import secrets
import threading
import time
from typing import Any, Iterator

from cachetools import TTLCache

_analysis_memory: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=512, ttl=24 * 3600)

_gemini_file_memory: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=256, ttl=24 * 3600)

_redis_cli: Any | None = None  # None uninitialized, False disabled

_LOCK_TTL_SEC = 300
_LOCK_WAIT_SEC = 125.0

_RATE_WINDOW_MS = 1000
_RATE_MAX_PER_WINDOW = 14
_RATE_ACQUIRE_MAX_WAIT_SEC = 45.0

_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


def redis_available() -> bool:
    return _redis() is not None


def _redis():
    global _redis_cli
    if _redis_cli is False:
        return None
    if _redis_cli is not None:
        return _redis_cli

    from app.core.config import settings

    url = (settings.REDIS_URL or "").strip()
    if not url:
        _redis_cli = False
        return None

    try:
        import redis as redis_sync

        cli = redis_sync.Redis.from_url(url, decode_responses=True)
        cli.ping()
        _redis_cli = cli
        return cli
    except Exception:
        _redis_cli = False
        return None


def media_url_digest(file_url: str) -> str:
    norm = (file_url or "").strip().encode("utf-8")
    return hashlib.sha256(norm).hexdigest()


def canonical_media_cache_key(file_url: str) -> str:
    return f"image_analysis:{media_url_digest(file_url)}"


def distributed_analysis_lock_key(file_url: str) -> str:
    return f"lock:image_analysis:{media_url_digest(file_url)}"


def gemini_file_cache_key(fragment: str) -> str:
    return f"gemini_file:{fragment}"


def get_cached_analysis(cache_key: str) -> dict[str, Any] | None:
    if cache_key in _analysis_memory:
        return dict(_analysis_memory[cache_key])
    cli = _redis()
    if cli:
        raw = cli.get(cache_key)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    return None


def set_cached_analysis(cache_key: str, payload: dict[str, Any]) -> None:
    snap = dict(payload)
    _analysis_memory[cache_key] = snap
    cli = _redis()
    if cli:
        cli.setex(cache_key, 24 * 3600, json.dumps(snap, ensure_ascii=False))


def get_cached_gemini_file(cache_key: str) -> dict[str, Any] | None:
    if cache_key in _gemini_file_memory:
        return dict(_gemini_file_memory[cache_key])
    cli = _redis()
    if cli:
        raw = cli.get(cache_key)
        if raw:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
    return None


def set_cached_gemini_file(cache_key: str, payload: dict[str, Any], ttl_seconds: int = 24 * 3600) -> None:
    snap = dict(payload)
    _gemini_file_memory[cache_key] = snap
    cli = _redis()
    if cli:
        cli.setex(cache_key, ttl_seconds, json.dumps(snap, ensure_ascii=False))


@contextlib.contextmanager
def distributed_analysis_lock(file_url: str) -> Iterator[None]:
    """
    Cross-worker mutex for the same media URL. If Redis is down, this is a no-op
    (in-process mutex in ai_service still applies).
    """
    cli = _redis()
    if not cli:
        yield
        return

    key = distributed_analysis_lock_key(file_url)
    token = secrets.token_hex(16)
    deadline = time.time() + _LOCK_WAIT_SEC
    acquired = False
    while time.time() < deadline:
        try:
            if cli.set(key, token, nx=True, ex=_LOCK_TTL_SEC):
                acquired = True
                break
        except Exception:
            yield
            return
        time.sleep(min(0.05 + random.random() * 0.12, max(0.0, deadline - time.time())))

    try:
        yield
    finally:
        if acquired:
            try:
                cli.eval(_RELEASE_LOCK_LUA, 1, key, token)
            except Exception:
                pass


def acquire_gemini_rate_slot() -> bool:
    """
    Global-ish rate limit across workers via Redis fixed time buckets.

    Without Redis there is **no cluster-wide rate limit**; concurrency is capped
    in-process via semaphore in AIService (`_get_gemini_async_sem`) instead.
    """
    cli = _redis()
    if not cli:
        return True
    start = time.time()
    while time.time() - start < _RATE_ACQUIRE_MAX_WAIT_SEC:
        bucket = int(time.time() * 1000 / _RATE_WINDOW_MS)
        k = f"gemini_rl:{bucket}"
        try:
            n = cli.incr(k)
            if n == 1:
                cli.pexpire(k, _RATE_WINDOW_MS + 200)
            if n <= _RATE_MAX_PER_WINDOW:
                return True
            cli.decr(k)
        except Exception:
            return True
        time.sleep(0.02 + random.random() * 0.06)
    return False


# ---- Circuit breaker (process-local fast path + Redis shared) ----
_local_breaker_open_until = 0.0
_BREAKER_GUARD = threading.Lock()


def gemini_breaker_is_open() -> bool:
    with _BREAKER_GUARD:
        local_open = time.time() < _local_breaker_open_until
    cli = _redis()
    if cli:
        try:
            raw = cli.get("gemini_circuit_open_until")
            if raw:
                return float(raw) > time.time()
        except Exception:
            pass
    return local_open


def gemini_breaker_trip(seconds: float = 60.0) -> None:
    global _local_breaker_open_until
    until = time.time() + seconds
    with _BREAKER_GUARD:
        _local_breaker_open_until = max(_local_breaker_open_until, until)
    cli = _redis()
    if cli:
        try:
            cli.setex("gemini_circuit_open_until", int(seconds + 5), str(until))
        except Exception:
            pass


def gemini_breaker_clear() -> None:
    global _local_breaker_open_until
    with _BREAKER_GUARD:
        _local_breaker_open_until = 0.0
    cli = _redis()
    if cli:
        try:
            cli.delete("gemini_circuit_open_until")
        except Exception:
            pass
