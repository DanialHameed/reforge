from __future__ import annotations

"""
Gemini service for ReForge.

This module centralizes the “one call generates everything” flow and guarantees
frontend-safe fallbacks when Gemini AI is unavailable, returns malformed JSON,
or exhausts quota.
"""

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from google import genai
from google.genai import types as genai_types

from app.core.ai_providers import resolve_gemini_key
from app.core.circuit_breaker import get_gemini_circuit_breaker
from app.core.fallbacks import get_all_platform_fallbacks
from app.core.gemini_config import GEMINI_MODEL_CHAIN
from app.core.prompts import ALL_PLATFORMS_PROMPT
from app.core.retry_engine import GeminiRetryEngine

logger = logging.getLogger(__name__)


class GeminiService:
    """
    Backward-compatible service wrapper around Gemini multimodal generation.

    Public method signatures must remain stable because Celery tasks and routes
    may import/call this service.
    """

    def __init__(self) -> None:
        self._breaker = get_gemini_circuit_breaker()
        # B-9: use the canonical AI provider resolver. The resolver enforces
        # the same rules for every provider (precedence ``GEMINI_API_KEY`` >
        # ``GOOGLE_API_KEY`` > late ``os.environ`` reads, with placeholder
        # rejection for values like "change-me" / "your-api-key-here") so a
        # misconfigured deployment cannot silently look "configured".
        api_key, source = resolve_gemini_key()
        self._api_key = api_key
        self._source = source
        if not self._api_key:
            logger.critical(
                "gemini.no_api_key — neither GEMINI_API_KEY nor GOOGLE_API_KEY "
                "is configured (or both normalized to a placeholder); Gemini "
                "calls will be skipped and the OpenRouter fallback (or static "
                "fallback content) will be used for every request. Set "
                "GEMINI_API_KEY in the environment to restore AI.",
                extra={"event": "gemini.no_api_key"},
            )

    async def analyze_and_generate(self, image_url: str, content_id: str) -> dict[str, Any]:
        """
        Analyze the image and generate all platform variants in one Gemini call.

        Never raises. Always returns a complete dict suitable for persistence and UI.
        """
        try:
            if not self._breaker.is_available():
                logger.warning(
                    "gemini circuit breaker open; returning fallbacks",
                    extra={"content_id": content_id, "reason": "circuit_breaker_open"},
                )
                return self._full_fallback("circuit_breaker_open")

            # B-9: short-circuit when no API key is configured. Iterating the
            # entire model chain would just produce N exceptions and waste the
            # circuit breaker budget.
            if not self._api_key:
                logger.error(
                    "gemini.skip_no_api_key — skipping Gemini and going to "
                    "OpenRouter/fallback because no API key is configured",
                    extra={"content_id": content_id, "reason": "no_api_key"},
                )
                return await self._fallback_via_openrouter(
                    image_url, content_id, "no_api_key"
                )

            for model_cfg in GEMINI_MODEL_CHAIN:
                try:
                    result = await asyncio.to_thread(
                        self._call_model_with_retry, image_url, model_cfg.name, content_id
                    )
                    self._breaker.record_success()
                    logger.info(
                        "ai.provider.used provider=gemini model=%s "
                        "content_id=%s key_source=%s",
                        model_cfg.name,
                        content_id,
                        self._source or "<unknown>",
                        extra={
                            "event": "ai.provider.used",
                            "provider": "gemini",
                            "model": model_cfg.name,
                            "content_id": content_id,
                        },
                    )
                    return result
                except Exception as exc:
                    self._breaker.record_failure()
                    logger.warning(
                        "gemini model attempt failed; falling back to next model",
                        extra={
                            "content_id": content_id,
                            "model_name": model_cfg.name,
                            "exc_type": type(exc).__name__,
                        },
                    )
                    continue

            logger.error(
                "all gemini models exhausted; attempting openrouter fallback",
                extra={"content_id": content_id, "reason": "all_models_exhausted"},
            )
            return await self._fallback_via_openrouter(
                image_url, content_id, "all_models_exhausted"
            )
        except Exception as exc:
            # Absolute safety net.
            logger.error(
                "analyze_and_generate unexpected failure; returning fallbacks",
                extra={"content_id": content_id, "exc_type": type(exc).__name__},
            )
            return self._full_fallback("unexpected_error")

    async def _fallback_via_openrouter(
        self, image_url: str, content_id: str, reason: str
    ) -> dict[str, Any]:
        """Attempt OpenRouter; if it also fails, return the full static fallback.

        Extracted from ``analyze_and_generate`` so the no-API-key path and the
        models-exhausted path share the exact same recovery sequence.
        """
        # Local import avoids circular import (OpenRouter reuses our prompt constant).
        from app.services.openrouter_service import get_openrouter_service

        try:
            openrouter = get_openrouter_service()
            or_result = await openrouter.analyze_and_generate(image_url, content_id)
            if isinstance(or_result, dict) and "fallback_reason" not in or_result:
                return or_result
            logger.error(
                "openrouter fallback failed; returning full fallback",
                extra={"content_id": content_id, "reason": reason},
            )
        except Exception as exc:
            logger.exception(
                "openrouter fallback raised; returning full fallback",
                extra={
                    "content_id": content_id,
                    "reason": reason,
                    "exc_type": type(exc).__name__,
                },
            )
        return self._full_fallback(reason)

    def _guess_mime_type(self, url: str, data: bytes) -> str:
        ul = url.lower().split("?")[0].rstrip("/")
        if ul.endswith(".mp4"):
            return "video/mp4"
        if ul.endswith(".webm"):
            return "video/webm"
        if ul.endswith(".mov"):
            return "video/quicktime"
        if ul.endswith(".png"):
            return "image/png"
        if ul.endswith(".gif"):
            return "image/gif"
        if ul.endswith(".webp"):
            return "image/webp"
        if ul.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if "/video/upload/" in url.lower() or ".cloudinary.com" in ul and "/video/" in ul:
            return "video/mp4"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return "video/mp4"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        return "image/jpeg"

    def _fetch_media(self, url: str) -> tuple[bytes, str]:
        is_cloudinary_video = "/video/upload/" in url.lower()
        ul = url.lower().split("?")[0]
        looks_video = any(ul.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".m4v", ".mpeg", ".mkv"))
        tout = 300.0 if (is_cloudinary_video or looks_video) else 60.0
        with httpx.Client(timeout=tout, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            data = bytes(r.content)
            ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        mime = ct if ct.startswith("image/") or ct.startswith("video/") else self._guess_mime_type(url, data)
        return data, mime

    def _call_model_with_retry(
        self,
        image_url: str,
        model_name: str,
        content_id: str,
    ) -> dict[str, Any]:

        def _attempt() -> dict[str, Any]:
            client = genai.Client(api_key=self._api_key)
            media_bytes, mime_type = self._fetch_media(image_url)

            response = client.models.generate_content(
                model=model_name,
                contents=[
                    genai_types.Part.from_bytes(
                        data=media_bytes,
                        mime_type=mime_type,
                    ),
                    ALL_PLATFORMS_PROMPT,
                ],
                config=genai_types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )
            raw = response.text
            if not raw or not raw.strip():
                raise ValueError("gemini returned empty response")
            return self._parse_and_recover(raw, content_id)

        return GeminiRetryEngine.execute(
            _attempt,
            model_name=model_name,
            operation="analyze_and_generate",
            on_http_429=lambda _exc: logger.info(
                "Gemini Rate Limit reached. Switching to next model..."
            ),
        )

    def _parse_and_recover(self, raw: str, content_id: str) -> dict[str, Any]:
        cleaned = re.sub(r"```(?:json)?\\s*|\\s*```", "", raw or "").strip()

        parsed: dict[str, Any] = {}
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            parsed = self._extract_partial_json(cleaned)

        if not parsed:
            logger.warning(
                "gemini json parse failure; returning full fallback",
                extra={"content_id": content_id, "reason": "json_parse_failure"},
            )
            return self._full_fallback("json_parse_failure")

        fallbacks = get_all_platform_fallbacks()
        for key in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
            if not parsed.get(key):
                logger.info(
                    "injecting platform fallback",
                    extra={"content_id": content_id, "missing_key": key},
                )
                parsed[key] = fallbacks[key]

        # Ensure image_analysis exists (frontend-safe).
        if not parsed.get("image_analysis"):
            parsed["image_analysis"] = {
                "description": "Content analysis unavailable",
                "mood": "neutral",
                "key_elements": [],
            }

        return parsed

    def _extract_partial_json(self, text: str) -> dict[str, Any]:
        start = (text or "").find("{")
        end = (text or "").rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _full_fallback(self, reason: str) -> dict[str, Any]:
        # B-9: emit a single, well-shaped log line so operators can grep
        # ``ai.provider.used provider=static_fallback`` to count "fake
        # success" events without scraping every worker log.
        logger.warning(
            "ai.provider.used provider=static_fallback reason=%s",
            reason,
            extra={
                "event": "ai.provider.used",
                "provider": "static_fallback",
                "reason": reason,
            },
        )
        return {
            "image_analysis": {
                "description": "Content analysis unavailable",
                "mood": "neutral",
                "key_elements": [],
            },
            "fallback_reason": reason,
            **get_all_platform_fallbacks(),
        }
