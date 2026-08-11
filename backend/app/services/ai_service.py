"""
Gemini-backed media analysis & platform variants using Google `google.genai`.

Features: model fallback chain, quota-aware backoff + jitter, parsing retry,
per-URL TTL caching, Redis distributed lock + optional cluster rate-limit,
Gemini uploaded-file reuse (24h), circuit breaker, bounded async concurrency.

`analyze_media` / `analyze_media_async` swallow fatal errors and return a safe fallback
`MediaAnalysis` so callers need not gate on exceptions for schema stability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

import ffmpeg
import httpx
from google import genai
from pydantic import ValidationError

from app.core.ai_providers import resolve_gemini_key
from app.core.config import settings
from app.services.analysis_cache import (
    acquire_gemini_rate_slot,
    canonical_media_cache_key,
    distributed_analysis_lock,
    gemini_breaker_clear,
    gemini_breaker_is_open,
    gemini_breaker_trip,
    gemini_file_cache_key,
    get_cached_analysis,
    get_cached_gemini_file,
    media_url_digest,
    set_cached_analysis,
    set_cached_gemini_file,
)
from app.services.ai_types import MediaAnalysis, PlatformContent, ServiceUnavailableError
from app.services.prompt_templates import PromptTemplateEngine, validate_output

log = logging.getLogger("reforge.ai")

_MODEL_CHAIN: list[str] = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"]
_MAX_TOTAL_GEMINI_CALLS = 6
_BASE_BACKOFF_S = 0.75
_QUOTA_TRIP_AFTER = 3

_gemini_async_sem: asyncio.Semaphore | None = None
_gemini_async_sem_guard = threading.Lock()


def _get_gemini_async_sem() -> asyncio.Semaphore:
    """
    Bounded async concurrency per worker. When Redis isn't configured, tighten the cap
    (cluster-wide concurrency is uncontrollable locally).
    """
    global _gemini_async_sem
    with _gemini_async_sem_guard:
        if _gemini_async_sem is None:
            n = 3 if (settings.REDIS_URL or "").strip() else 2
            _gemini_async_sem = asyncio.Semaphore(n)
        return _gemini_async_sem


def _setup_metrics_logger() -> logging.Logger:
    logger = logging.getLogger("reforge.ai.gemini_file")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    # parents[2] is the app root in both layouts this runs under: locally
    # that's `backend/` (.../backend/app/services/ai_service.py), and in the
    # Docker image it's `/app` (Dockerfile does `COPY . /app`, flattening
    # `backend/` itself out of the path). The previous `parents[3]` climbed
    # one level too far in the container — landing on `/` — which the
    # non-root `reforge` user can't write to, crashing every worker on
    # import with a silent `PermissionError` before logging was even set up.
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        filename=str(log_dir / "gemini.log"),
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


_metrics = _setup_metrics_logger()


def _json_event(kind: str, **fields: Any) -> None:
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
    try:
        log.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        log.info("%s %s", kind, fields)


def _safe_trim(s: str, n: int = 500) -> str:
    t = (s or "").strip()
    return t if len(t) <= n else t[:n] + "…"


def _extract_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if not s:
        raise ValueError("Empty model response")

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    if "```" in s:
        for p in s.split("```"):
            p2 = p.strip()
            if not p2:
                continue
            if p2.lower().startswith("json"):
                p2 = p2[4:].strip()
            try:
                obj = json.loads(p2)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue

    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        obj = json.loads(s[start : end + 1])
        if isinstance(obj, dict):
            return obj

    raise ValueError("Could not parse JSON from model response")


def _platform_fallback(platform: str) -> dict[str, Any]:
    p = (platform or "").strip().lower()
    if p == "instagram":
        return {"caption": "Check out this amazing content! 🚀✨", "hashtags": ["#content", "#reforge"], "story_text": ""}
    if p == "youtube":
        return {
            "title": "New content drop",
            "description": "Unable to generate a full description right now. Please try again shortly.\n\n#content #reforge",
            "tags": ["content", "reforge"],
        }
    if p == "twitter":
        return {"tweet": "Check out this amazing content!", "thread": []}
    if p == "linkedin":
        return {"post": "Sharing something interesting today—check it out.", "hashtags": ["#content", "#reforge"]}
    if p == "facebook":
        return {"post": "Check out this amazing content!", "hashtags": ["#content", "#reforge"]}
    return {}


def _strict_analysis_prompt(kind: str) -> str:
    return (
        f"Analyze this {kind}. Return JSON with keys:\n"
        '- description: string (2-3 sentences)\n'
        "- main_topic: string\n"
        "- themes: array of 3-5 strings\n"
        '- mood: one of ["professional", "casual", "educational", "entertaining", "inspirational"]\n'
        "- target_audience: string\n"
        '- content_rating: one of ["general", "mature"]\n'
        "- key_points: array of up to 5 strings\n\n"
        "mood must be EXACTLY one of:\n"
        '["professional", "casual", "educational", "entertaining", "inspirational"]\n\n'
        "Return ONLY valid JSON.\n"
        "All enum values MUST be lowercase.\n"
        "Do NOT generate any extra values.\n"
    )


def _normalize_media_analysis_payload(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "description": "No description available",
        "main_topic": "general",
        "themes": [],
        "mood": "casual",
        "target_audience": "general",
        "content_rating": "general",
        "key_points": [],
        "analysis_status": "ok",
    }
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if v is not None})

    out["description"] = str(out.get("description") or "No description available").strip() or "No description available"
    out["main_topic"] = str(out.get("main_topic") or "general").strip() or "general"
    out["target_audience"] = str(out.get("target_audience") or "general").strip() or "general"
    out["content_rating"] = str(out.get("content_rating") or "general").lower().strip() or "general"
    if out["content_rating"] not in {"general", "mature"}:
        out["content_rating"] = "general"

    out["mood"] = str(out.get("mood", "")).lower().strip()
    allowed = {"professional", "casual", "educational", "entertaining", "inspirational"}
    if out["mood"] not in allowed:
        out["mood"] = "casual"

    themes = out.get("themes", [])
    if isinstance(themes, str):
        themes = [t.strip() for t in themes.split(",") if t.strip()]
    if not isinstance(themes, list):
        themes = []
    themes = [str(t).strip() for t in themes if str(t).strip()]
    if not themes:
        themes = ["general"]
    out["themes"] = themes

    kps = out.get("key_points", [])
    if isinstance(kps, str):
        kps = [x.strip() for x in kps.split("\n") if x.strip()]
    if not isinstance(kps, list):
        kps = []
    out["key_points"] = [str(k).strip() for k in kps if str(k).strip()]

    st = out.get("analysis_status", "ok")
    if st not in {"ok", "fallback"}:
        out["analysis_status"] = "ok"
    return out


def _fallback_media_analysis() -> MediaAnalysis:
    return MediaAnalysis.model_validate(
        _normalize_media_analysis_payload(
            {
                "description": "Unable to analyze image at the moment",
                "main_topic": "unknown",
                "themes": [],
                "mood": "casual",
                "target_audience": "general",
                "content_rating": "general",
                "key_points": [],
                "analysis_status": "fallback",
            }
        )
    )


_http_clients: dict[int, httpx.Client] = {}
_http_lock = threading.Lock()


def _thread_http_client() -> httpx.Client:
    tid = threading.get_ident()
    with _http_lock:
        c = _http_clients.get(tid)
        if c is None:
            c = httpx.Client(
                timeout=httpx.Timeout(120.0, connect=20.0),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            )
            _http_clients[tid] = c
        return c


def _parse_retry_delay_seconds(exc: BaseException) -> float | None:
    try:
        d = getattr(exc, "details", None)
        blob = ""
        if isinstance(d, dict):
            blob = json.dumps(d)
        elif d is not None:
            blob = str(d)

        blob_l = blob.lower()
        for pat in (
            r'"retryDelay"\s*:\s*"([0-9.]+)\s*s"',
            r'"retry_delay"\s*:\s*{[^}]*"seconds"\s*:\s*([0-9.]+)',
        ):
            m = re.search(pat, blob_l.replace("'", '"'))
            if m:
                return float(m.group(1))
    except Exception:
        pass

    m2 = re.search(r"retry in\s+([0-9.]+)\s*s", str(exc).lower())
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            return None

    resp = getattr(exc, "response", None)
    if resp is not None:
        hdrs = getattr(resp, "headers", {}) or {}
        ra = hdrs.get("retry-after") or hdrs.get("Retry-After")
        if ra:
            try:
                return float(str(ra).strip())
            except ValueError:
                pass
    return None


def _is_quota(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    try:
        if int(code) == 429:
            return True
    except Exception:
        pass
    st = str(getattr(exc, "status", "") or "").upper()
    msg = str(exc).upper()
    return "RESOURCE_EXHAUSTED" in msg or st == "RESOURCE_EXHAUSTED" or "429" in msg


def _is_not_found(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    try:
        if int(code) == 404:
            return True
    except Exception:
        pass
    return "NOT_FOUND" in str(exc).upper() or "NOT FOUND" in str(exc).upper()


def _resp_text(resp: Any) -> str:
    txt = getattr(resp, "text", None)
    if txt:
        return str(txt).strip()
    cand = getattr(resp, "candidates", None)
    chunks: list[str] = []
    if cand:
        try:
            c0 = cand[0]
            parts = getattr(getattr(c0, "content", None), "parts", None) or []
            for p in parts:
                t = getattr(p, "text", None)
                if t:
                    chunks.append(str(t))
        except Exception:
            pass
    if chunks:
        return "\n".join(chunks).strip()
    return str(resp).strip()


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[Any] = [None]
    err: list[BaseException | None] = [None]

    def runner():
        try:
            result[0] = asyncio.run(coro)
        except BaseException as e:
            err[0] = e

    t = threading.Thread(target=runner, name="reforge-asyncio-bridge", daemon=True)
    t.start()
    t.join()
    if err[0] is not None:
        raise err[0]
    return result[0]


_lock_registry_guard = threading.Lock()
_lock_registry: "OrderedDict[str, threading.Lock]" = OrderedDict()
_MAX_LOCK_KEYS = 256


def _mutex_for(key: str) -> threading.Lock:
    with _lock_registry_guard:
        if key in _lock_registry:
            _lock_registry.move_to_end(key)
            return _lock_registry[key]
        lk = threading.Lock()
        _lock_registry[key] = lk
        while len(_lock_registry) > _MAX_LOCK_KEYS:
            _lock_registry.popitem(last=False)
        return lk


class AIService:
    MODEL: str
    VIDEO_MODEL: str

    def __init__(self) -> None:
        # B-9: route through the canonical resolver. The duplicate
        # ``settings.gemini_api_key or os.getenv(...)`` fallback that used to
        # live here was dead code (Settings already does the late env read)
        # AND it bypassed the placeholder/conflict detection that the rest
        # of the system now relies on. ``resolve_gemini_key`` enforces a
        # single rule for every consumer.
        key, _source = resolve_gemini_key()
        if not key:
            raise RuntimeError(
                "Missing GEMINI_API_KEY (or GOOGLE_API_KEY) for Gemini API. "
                "Set one before constructing AIService."
            )

        self._client = genai.Client(api_key=key)
        self._templates = PromptTemplateEngine()
        self.MODEL = _MODEL_CHAIN[0]
        self.VIDEO_MODEL = self.MODEL

    def analyze_media(self, file_url: str, file_type: str) -> MediaAnalysis:
        return _run_sync(self.analyze_media_async(file_url, file_type))

    async def analyze_media_async(self, file_url: str, file_type: str) -> MediaAnalysis:
        try:
            async with _get_gemini_async_sem():
                if file_type == "video":
                    return await asyncio.to_thread(self._analyze_video_blocking, file_url)
                if file_type == "image":
                    return await asyncio.to_thread(self._analyze_image_blocking, file_url)
                raise ValueError("file_type must be 'video' or 'image'")
        except Exception as e:
            _json_event("analyze_media_failed", error=type(e).__name__, message=str(e)[:400])
            return _fallback_media_analysis()

    def _dedupe_cache(self, file_url: str, work: Callable[[], MediaAnalysis]) -> MediaAnalysis:
        key = canonical_media_cache_key(file_url)
        cached = get_cached_analysis(key)
        if cached:
            try:
                return MediaAnalysis.model_validate(_normalize_media_analysis_payload(cached))
            except ValidationError:
                pass

        with distributed_analysis_lock(file_url):
            mtx = _mutex_for(key)
            with mtx:
                cached2 = get_cached_analysis(key)
                if cached2:
                    try:
                        return MediaAnalysis.model_validate(_normalize_media_analysis_payload(cached2))
                    except ValidationError:
                        pass
                out = work()
                try:
                    set_cached_analysis(key, json.loads(out.model_dump_json()))
                except Exception:
                    pass
                return out

    def _get_or_upload_gemini_file(self, local_path: str, cache_fragment: str) -> Any:
        ck = gemini_file_cache_key(cache_fragment)
        cached = get_cached_gemini_file(ck)
        name = (cached or {}).get("name") if isinstance(cached, dict) else None
        if name:
            try:
                fo = self._client.files.get(name=name)
                _json_event("gemini_file_cache_hit", digest=_safe_trim(cache_fragment, 24))
                return fo
            except Exception as exc:
                _json_event("gemini_file_cache_stale", digest=_safe_trim(cache_fragment, 24), err=type(exc).__name__)

        uploaded = self._client.files.upload(file=local_path)
        nm = getattr(uploaded, "name", None)
        if nm:
            set_cached_gemini_file(ck, {"name": nm})
        _json_event("gemini_file_cache_miss_upload", digest=_safe_trim(cache_fragment, 24))
        return uploaded

    def _generate_with_chain(self, contents_fn: Callable[[str], list[Any]]) -> str:
        model_idx = 0
        attempt = 0
        total_calls = 0
        consecutive_quota = 0

        while total_calls < _MAX_TOTAL_GEMINI_CALLS and model_idx < len(_MODEL_CHAIN):
            if gemini_breaker_is_open():
                _json_event("gemini_circuit_block")
                raise ServiceUnavailableError("Gemini temporarily unavailable (circuit open).")

            if not acquire_gemini_rate_slot():
                _json_event("gemini_rate_slot_exhausted")
                gemini_breaker_trip(30.0)
                raise ServiceUnavailableError("Gemini rate limiter saturated.")

            model = _MODEL_CHAIN[model_idx]
            contents = contents_fn(model)
            t0 = time.perf_counter()
            try:
                resp = self._client.models.generate_content(model=model, contents=contents)
                text = _resp_text(resp)
                ms = int((time.perf_counter() - t0) * 1000)
                total_calls += 1
                consecutive_quota = 0
                gemini_breaker_clear()
                _metrics.info(
                    json.dumps(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "model": model,
                            "latency_ms": ms,
                            "gemini_calls": total_calls,
                        },
                        ensure_ascii=False,
                    )
                )
                return text
            except Exception as exc:
                total_calls += 1
                _json_event(
                    "gemini_call_error",
                    model=model,
                    attempt=attempt,
                    total_calls=total_calls,
                    err_type=type(exc).__name__,
                )

                if _is_not_found(exc):
                    consecutive_quota = 0
                    _json_event("model_rotate_404", model=model)
                    model_idx += 1
                    attempt = 0
                    continue

                if _is_quota(exc):
                    consecutive_quota += 1
                    if consecutive_quota >= _QUOTA_TRIP_AFTER:
                        gemini_breaker_trip(60.0)
                        _json_event("gemini_circuit_trip_quota", model=model, consecutive=consecutive_quota)

                    delay_api = _parse_retry_delay_seconds(exc)
                    exp = _BASE_BACKOFF_S * (2 ** min(attempt, 4)) + random.uniform(0, 1.0)
                    delay = exp if delay_api is None else max(delay_api, 0.0) + random.uniform(0, 0.75)
                    delay = min(delay, 5.0)
                    _json_event(
                        "gemini_quota_sleep",
                        seconds=round(delay, 3),
                        model=model,
                        api_delay=(None if delay_api is None else round(delay_api, 3)),
                    )
                    time.sleep(delay)

                    should_rotate = (delay_api is not None and delay_api >= 2.0) or attempt >= 2
                    if should_rotate and model_idx < len(_MODEL_CHAIN) - 1:
                        _json_event("model_rotate_quota", from_m=model, to_m=_MODEL_CHAIN[model_idx + 1])
                        model_idx += 1
                        attempt = 0
                    else:
                        attempt += 1
                    continue

                consecutive_quota = 0
                delay = _BASE_BACKOFF_S * (2 ** min(attempt, 4)) + random.uniform(0, 1.0)
                delay = min(delay, 5.0)
                _json_event("gemini_transient_sleep", seconds=round(delay, 3), model=model)
                time.sleep(delay)
                attempt += 1

        raise ServiceUnavailableError("Gemini unavailable after capped attempts.")

    def _media_from_raw(self, raw: str) -> MediaAnalysis:
        try:
            data = _extract_json(raw)
        except Exception as first:
            _json_event("json_parse_failed", err=str(first)[:240])
            raise RuntimeError("Gemini analysis response was not valid JSON.") from first

        normalized = _normalize_media_analysis_payload(data)
        _json_event(
            "analysis_normalized",
            main_topic=_safe_trim(str(normalized.get("main_topic", "")), 120),
            themes_count=len(normalized.get("themes") or []),
        )
        try:
            return MediaAnalysis.model_validate(normalized)
        except ValidationError as ve:
            _json_event("analysis_validation_error", err=str(ve)[:500])
            return MediaAnalysis.model_validate(_normalize_media_analysis_payload(normalized))

    def _analyze_image_blocking(self, file_url: str) -> MediaAnalysis:
        def inner() -> MediaAnalysis:
            prompt = _strict_analysis_prompt("image")
            path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
            try:
                self._download_to_path(file_url, path)
                up = self._get_or_upload_gemini_file(path, media_url_digest(file_url))

                def contents_fn(model: str) -> list[Any]:
                    return [prompt, up]

                raw = self._generate_with_chain(contents_fn)
                _json_event("analysis_raw_image", raw=_safe_trim(raw))
                try:
                    return self._media_from_raw(raw)
                except RuntimeError:
                    raw2 = self._generate_with_chain(contents_fn)
                    _json_event("analysis_raw_image_retry", raw=_safe_trim(raw2))
                    return self._media_from_raw(raw2)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        return self._dedupe_cache(file_url, inner)

    def _analyze_video_blocking(self, file_url: str) -> MediaAnalysis:
        def inner() -> MediaAnalysis:
            prompt = _strict_analysis_prompt("video")
            video_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
            video_path = video_tmp.name
            video_tmp.close()
            uploads: list[Any] = []
            try:
                self._download_to_path(file_url, video_path)
                duration = self._probe_duration_seconds(video_path)
                if duration <= 0:
                    raise RuntimeError("Could not determine video duration.")

                with tempfile.TemporaryDirectory(prefix="reforge_vf_") as td:
                    ts = [duration * 0.10, duration * 0.50, duration * 0.90]
                    frame_paths: list[str] = []
                    for i, t in enumerate(ts, start=1):
                        fp = os.path.join(td, f"f_{i}.jpg")
                        self._extract_frame(video_path, fp, timestamp_seconds=t)
                        frame_paths.append(fp)
                    url_d = media_url_digest(file_url)
                    uploads = [
                        self._get_or_upload_gemini_file(p, f"{url_d}:vf:{i}")
                        for i, p in enumerate(frame_paths, start=1)
                    ]

                def contents_fn(model: str) -> list[Any]:
                    return [prompt, *uploads]

                raw = self._generate_with_chain(contents_fn)
                _json_event("analysis_raw_video", raw=_safe_trim(raw))
                try:
                    return self._media_from_raw(raw)
                except RuntimeError:
                    raw2 = self._generate_with_chain(contents_fn)
                    _json_event("analysis_raw_video_retry", raw=_safe_trim(raw2))
                    return self._media_from_raw(raw2)
            finally:
                try:
                    os.remove(video_path)
                except OSError:
                    pass

        return self._dedupe_cache(file_url, inner)

    def _download_to_path(self, url: str, out_path: str) -> None:
        suffix = ""
        if "." in url.split("?")[0].rsplit("/", 1)[-1]:
            suffix = "." + url.split("?")[0].rsplit(".", 1)[-1]
        if suffix and not out_path.endswith(suffix):
            out_path = out_path + suffix

        c = _thread_http_client()
        r = c.get(url)
        r.raise_for_status()
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(r.content)

    def _probe_duration_seconds(self, video_path: str) -> float:
        try:
            info = ffmpeg.probe(video_path)
            fmt = info.get("format") or {}
            dur = fmt.get("duration")
            return float(dur) if dur is not None else 0.0
        except Exception:
            return 0.0

    def _extract_frame(self, video_path: str, out_jpeg_path: str, timestamp_seconds: float) -> None:
        (
            ffmpeg.input(video_path, ss=max(0.0, float(timestamp_seconds)))
            .output(out_jpeg_path, vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(quiet=True)
        )

    async def generate_platform_variants(
        self, analysis: MediaAnalysis, user_prefs: dict
    ) -> dict[str, PlatformContent]:
        # Backwards-compatible entrypoint: now uses a single combined Gemini call.
        return await self.generate_all_platform_variants(analysis, user_prefs=user_prefs)

    async def generate_all_platform_variants(
        self, analysis: MediaAnalysis, user_prefs: dict | None = None
    ) -> dict[str, PlatformContent]:
        """
        Generate all platform variants in ONE Gemini request.

        Returns the same shape as `generate_platform_variants`:
        { "youtube": PlatformContent, "instagram": PlatformContent, ... }.
        """
        prefs = user_prefs or {}

        yt_rules = self._templates.youtube_title_prompt(analysis, prefs)
        ig_rules = self._templates.instagram_caption_prompt(analysis, prefs)
        tw_rules = self._templates.twitter_hook_prompt(analysis, prefs)
        li_rules = self._templates.linkedin_authority_prompt(analysis, prefs)
        fb_rules = self._templates.facebook_friend_prompt(analysis, prefs)

        prompt = (
            "Return ONLY valid JSON (no markdown) with this exact top-level schema:\n"
            "{\n"
            '  "instagram": {"caption": "...", "hashtags": ["..."], "story_text": "..."},\n'
            '  "twitter": {"tweet": "...", "thread": ["...", "...", "..."]},\n'
            '  "linkedin": {"post": "...", "hashtags": ["..."]},\n'
            '  "facebook": {"post": "...", "hashtags": ["..."]},\n'
            '  "youtube": {"title": "...", "description": "...", "tags": ["..."]}\n'
            "}\n\n"
            "PLATFORM RULES (must follow exactly):\n"
            "YOUTUBE RULES:\n"
            "- title: string (<=100 chars)\n"
            "- description: string (800+ chars, include '00:00 - ...' timestamp placeholders, end with 10 hashtags)\n"
            "- tags: array of 15 strings\n"
            f"{yt_rules}\n\n"
            "INSTAGRAM RULES:\n"
            "- caption: string\n"
            "- hashtags: array of 30 strings\n"
            "- story_text: string (max 15 words)\n"
            f"{ig_rules}\n\n"
            "TWITTER RULES:\n"
            "- tweet: string (max 280 chars)\n"
            "- thread: array of 3 strings (each max 280 chars)\n"
            f"{tw_rules}\n\n"
            "LINKEDIN RULES:\n"
            "- post: string\n"
            "- hashtags: array of 3-5 strings\n"
            f"{li_rules}\n\n"
            "FACEBOOK RULES:\n"
            "- post: string (100-200 chars)\n"
            "- hashtags: array of up to 3 strings\n"
            f"{fb_rules}\n\n"
            "Now return the full JSON object.\n"
        )

        def _all_fallback() -> dict[str, dict[str, Any]]:
            return {
                "instagram": _platform_fallback("instagram"),
                "twitter": _platform_fallback("twitter"),
                "linkedin": _platform_fallback("linkedin"),
                "facebook": _platform_fallback("facebook"),
                "youtube": _platform_fallback("youtube"),
            }

        def _normalize_sections(obj: Any) -> dict[str, dict[str, Any]]:
            base = _all_fallback()
            if not isinstance(obj, dict):
                return base
            for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
                v = obj.get(k)
                if isinstance(v, dict):
                    merged = dict(base[k])
                    merged.update(v)
                    base[k] = merged
            # Ensure non-empty primary fields.
            if not str(base["instagram"].get("caption", "")).strip():
                base["instagram"] = _platform_fallback("instagram")
            if not str(base["twitter"].get("tweet", "")).strip():
                base["twitter"] = _platform_fallback("twitter")
            if not str(base["linkedin"].get("post", "")).strip():
                base["linkedin"] = _platform_fallback("linkedin")
            if not str(base["facebook"].get("post", "")).strip():
                base["facebook"] = _platform_fallback("facebook")
            if not str(base["youtube"].get("title", "")).strip():
                base["youtube"] = _platform_fallback("youtube")
            return base

        def _call_once() -> str:
            def contents_fn(model: str) -> list[Any]:
                return [prompt]

            try:
                return self._generate_with_chain(contents_fn)
            except ServiceUnavailableError as exc:
                # Never hard-fail the Celery pipeline; return a full fallback payload fast.
                _json_event("all_platform_gemini_exhausted_fallback", err=str(exc)[:240])
                return json.dumps(_all_fallback(), ensure_ascii=False)

        raw: str | None = None
        parsed: dict[str, Any] | None = None
        async with _get_gemini_async_sem():
            raw = await asyncio.to_thread(_call_once)
        try:
            parsed = _extract_json(raw)
        except Exception as first:
            _json_event("all_platform_json_retry", err=str(first)[:260])
            async with _get_gemini_async_sem():
                raw2 = await asyncio.to_thread(_call_once)
            try:
                parsed = _extract_json(raw2)
            except Exception as second:
                _json_event("all_platform_json_fallback", err=type(second).__name__)
                parsed = _all_fallback()

        sections = _normalize_sections(parsed)

        return {
            "youtube": PlatformContent(
                platform="youtube",
                payload={
                    "title": str(sections["youtube"].get("title", "")).strip(),
                    "description": str(sections["youtube"].get("description", "")).strip(),
                    "tags": sections["youtube"].get("tags", []),
                },
            ),
            "instagram": PlatformContent(
                platform="instagram",
                payload={
                    "caption": str(sections["instagram"].get("caption", "")).strip(),
                    "hashtags": sections["instagram"].get("hashtags", []),
                    "story_text": str(sections["instagram"].get("story_text", "")).strip(),
                },
            ),
            "twitter": PlatformContent(
                platform="twitter",
                payload={
                    "tweet": str(sections["twitter"].get("tweet", "")).strip(),
                    "thread": sections["twitter"].get("thread", []),
                },
            ),
            "linkedin": PlatformContent(
                platform="linkedin",
                payload={
                    "post": str(sections["linkedin"].get("post", "")).strip(),
                    "hashtags": sections["linkedin"].get("hashtags", []),
                },
            ),
            "facebook": PlatformContent(
                platform="facebook",
                payload={
                    "post": str(sections["facebook"].get("post", "")).strip(),
                    "hashtags": sections["facebook"].get("hashtags", []),
                },
            ),
        }

    async def generate_youtube_content(self, analysis: MediaAnalysis, user_prefs: dict | None = None) -> PlatformContent:
        prefs = user_prefs or {}
        title_prompt = self._templates.youtube_title_prompt(analysis, prefs)
        prompt = (
            "Return ONLY valid JSON with keys:\n"
            "- title: string\n"
            "- description: string (800+ chars, include '00:00 - ...' timestamp placeholders, end with 10 hashtags)\n"
            "- tags: array of 15 strings (mix broad + niche)\n\n"
            "TITLE RULES (must follow exactly):\n"
            f"{title_prompt}\n"
            "Now generate the full JSON.\n"
        )
        payload = await self._best_effort_validated_json(platform="youtube", prompt=prompt, validate_field="title")
        return PlatformContent(
            platform="youtube",
            payload={
                "title": str(payload.get("title", "")).strip(),
                "description": str(payload.get("description", "")).strip(),
                "tags": payload.get("tags", []),
            },
        )

    async def generate_instagram_content(self, analysis: MediaAnalysis, user_prefs: dict | None = None) -> PlatformContent:
        prefs = user_prefs or {}
        caption_prompt = self._templates.instagram_caption_prompt(analysis, prefs)
        prompt = (
            "Return ONLY valid JSON with keys:\n"
            "- caption: string\n"
            "- hashtags: array of 30 strings\n"
            "- story_text: string (max 15 words)\n\n"
            "CAPTION RULES (must follow exactly):\n"
            f"{caption_prompt}\n"
            "Now generate the full JSON.\n"
        )
        payload = await self._best_effort_validated_json(platform="instagram", prompt=prompt, validate_field="caption")
        return PlatformContent(
            platform="instagram",
            payload={
                "caption": str(payload.get("caption", "")).strip(),
                "hashtags": payload.get("hashtags", []),
                "story_text": str(payload.get("story_text", "")).strip(),
            },
        )

    async def generate_twitter_content(self, analysis: MediaAnalysis, user_prefs: dict | None = None) -> PlatformContent:
        prefs = user_prefs or {}
        hook_prompt = self._templates.twitter_hook_prompt(analysis, prefs)
        prompt = (
            "Return ONLY valid JSON with keys:\n"
            "- tweet: string (max 280 chars)\n"
            "- thread: array of 3 strings (each max 280 chars)\n\n"
            "FIRST TWEET RULES (must follow exactly):\n"
            f"{hook_prompt}\n"
            "Now generate the full JSON.\n"
        )
        payload = await self._best_effort_validated_json(platform="twitter", prompt=prompt, validate_field="tweet")
        return PlatformContent(
            platform="twitter",
            payload={"tweet": str(payload.get("tweet", "")).strip(), "thread": payload.get("thread", [])},
        )

    async def generate_linkedin_content(self, analysis: MediaAnalysis, user_prefs: dict | None = None) -> PlatformContent:
        prefs = user_prefs or {}
        post_prompt = self._templates.linkedin_authority_prompt(analysis, prefs)
        prompt = (
            "Return ONLY valid JSON with keys:\n"
            "- post: string\n"
            "- hashtags: array of 3-5 strings\n\n"
            "POST RULES (must follow exactly):\n"
            f"{post_prompt}\n"
            "Now generate the full JSON.\n"
        )
        payload = await self._best_effort_validated_json(platform="linkedin", prompt=prompt, validate_field="post")
        return PlatformContent(
            platform="linkedin",
            payload={"post": str(payload.get("post", "")).strip(), "hashtags": payload.get("hashtags", [])},
        )

    async def generate_facebook_content(self, analysis: MediaAnalysis, user_prefs: dict | None = None) -> PlatformContent:
        prefs = user_prefs or {}
        post_prompt = self._templates.facebook_friend_prompt(analysis, prefs)
        prompt = (
            "Return ONLY valid JSON with keys:\n"
            "- post: string (100-200 chars)\n"
            "- hashtags: array of up to 3 strings\n\n"
            "POST RULES (must follow exactly):\n"
            f"{post_prompt}\n"
            "Now generate the full JSON.\n"
        )
        payload = await self._best_effort_validated_json(platform="facebook", prompt=prompt, validate_field="post")
        return PlatformContent(
            platform="facebook",
            payload={"post": str(payload.get("post", "")).strip(), "hashtags": payload.get("hashtags", [])},
        )

    async def _generate_json(self, prompt: str, *, platform: str | None = None) -> dict[str, Any]:
        def _call() -> str:
            def contents_fn(model: str) -> list[Any]:
                return [prompt]

            return self._generate_with_chain(contents_fn)

        async with _get_gemini_async_sem():
            raw = await asyncio.to_thread(_call)
        try:
            return _extract_json(raw)
        except Exception as first:
            _json_event("platform_json_retry", err=str(first)[:260])
            async with _get_gemini_async_sem():
                raw2 = await asyncio.to_thread(_call)
            try:
                return _extract_json(raw2)
            except Exception as second:
                _json_event(
                    "platform_json_fallback",
                    platform=(platform or ""),
                    err=type(second).__name__,
                )
                return _platform_fallback(platform or "")

    async def _best_effort_validated_json(
        self, *, platform: str, prompt: str, validate_field: str
    ) -> dict[str, Any]:
        payload_1: dict[str, Any] = {}
        try:
            payload_1 = await self._generate_json(prompt, platform=platform)
        except Exception:
            try:
                async with _get_gemini_async_sem():

                    def _call2() -> str:
                        def contents_fn(model: str) -> list[Any]:
                            return [prompt]

                        return self._generate_with_chain(contents_fn)

                    raw = await asyncio.to_thread(_call2)
                payload_1 = {validate_field: _safe_trim(raw, 4000)}
            except Exception:
                return _platform_fallback(platform)

        value_1 = str(payload_1.get(validate_field, "")).strip()
        if not value_1:
            return _platform_fallback(platform)
        vr_1 = validate_output(platform, {validate_field: value_1})
        if vr_1.is_valid:
            payload_1[validate_field] = value_1
            return payload_1

        strict_prompt = (
            f"{prompt}\n\nFix the issues: {vr_1.violations}. Follow all rules strictly.\nReturn ONLY the corrected JSON.\n"
        )
        payload_2: dict[str, Any] = {}
        try:
            payload_2 = await self._generate_json(strict_prompt, platform=platform)
        except Exception:
            payload_2 = {}

        value_2 = str(payload_2.get(validate_field, "")).strip() if payload_2 else ""
        vr_2 = validate_output(platform, {validate_field: value_2})
        if vr_2.is_valid and payload_2:
            payload_2[validate_field] = value_2
            return payload_2

        if value_2 and len(value_2) >= max(1, len(value_1) // 2):
            payload_2[validate_field] = value_2
            return payload_2

        payload_1[validate_field] = value_1
        return payload_1 if value_1 else _platform_fallback(platform)
