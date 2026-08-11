"""
OpenRouter fallback when Gemini quota/errors exhaust.

OpenAI-compatible Chat Completions at POST /api/v1/chat/completions.
Tries multiple vision-capable models — a single stale slug often returns 404.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from typing import Any

import httpx

from app.core.ai_providers import resolve_openrouter_key
from app.core.fallbacks import get_all_platform_fallbacks
from app.core.prompts import ALL_PLATFORMS_PROMPT

logger = logging.getLogger(__name__)


# OpenRouter vision models accept these as inline ``data:`` URIs. Anything
# outside this set (notably video/*) is rejected by every model in the chain,
# so we short-circuit to the static fallback rather than wasting tokens
# uploading bytes the model will refuse.
_OPENROUTER_SUPPORTED_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

# Explicit extension-to-MIME map — `mimetypes.guess_type` does not know
# ``.webp`` on CPython <= 3.10 (the production runtime is pinned to 3.11
# but the test/dev hosts still routinely run 3.10), so we cover it here
# alongside the common image extensions.
_OPENROUTER_IMAGE_EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _resolve_openrouter_image_mime(
    image_url: str, response_content_type: str | None
) -> str | None:
    """
    Resolve a vision-model-acceptable image MIME for the downloaded asset.

    Resolution order (B-13):
        1. ``Content-Type`` returned by the origin.
        2. ``mimetypes.guess_type`` against the URL path (Cloudinary signed
           URLs frequently strip Content-Type to ``application/octet-stream``).
        3. ``image/jpeg`` only when the URL clearly looks like an image
           (``image/*`` Content-Type without a recognised subtype).

    Returns ``None`` when the asset is non-image (e.g. ``video/mp4``) so the
    caller can bypass the model call and hand back the static fallback.

    Earlier versions hard-coded ``data:image/jpeg;base64,...`` regardless of
    the actual asset MIME, which silently truncated PNG / WebP analysis quality
    and pushed video bytes into vision endpoints that always 415'd.
    """
    ct = (response_content_type or "").split(";")[0].strip().lower()
    if ct in _OPENROUTER_SUPPORTED_IMAGE_MIMES:
        return ct

    base = image_url.split("?", 1)[0].lower()
    for ext, mapped in _OPENROUTER_IMAGE_EXT_TO_MIME.items():
        if base.endswith(ext):
            return mapped

    guess, _ = mimetypes.guess_type(base)
    if guess:
        guess = guess.split(";")[0].strip().lower()
        if guess in _OPENROUTER_SUPPORTED_IMAGE_MIMES:
            return guess

    if ct.startswith("video/"):
        return None
    if ct.startswith("image/"):
        return "image/jpeg"
    return None

# Vision-capable OpenRouter models to try in order (update if slugs change on openrouter.ai).
OPENROUTER_VISION_MODEL_CHAIN: tuple[str, ...] = (
    "google/gemini-2.0-flash-001",
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "qwen/qwen2-vl-7b-instruct",
    "mistralai/pixtral-12b",
)


class OpenRouterService:
    AVAILABLE_MODELS = {
        "llama_vision_free": "meta-llama/llama-3.2-11b-vision-instruct:free",
        "qwen": "qwen/qwen2-vl-7b-instruct",
        "gemini_or": "google/gemini-2.0-flash-001",
        "gpt4o_mini": "openai/gpt-4o-mini",
        "pixtral": "mistralai/pixtral-12b",
    }

    def __init__(self) -> None:
        # B-9: read through the canonical resolver instead of ``os.getenv``.
        # The resolver enforces the same rules as Gemini (placeholder rejection,
        # late-env fallback, Settings precedence) so the OpenRouter fallback
        # cannot silently "be configured" when the operator pasted
        # ``OPENROUTER_API_KEY=your-key-here``. ``self._source`` lets us log
        # which env-var actually contributed the active value without ever
        # logging the value itself.
        api_key, source = resolve_openrouter_key()
        self.api_key = api_key
        self._source = source
        self.base_url = "https://openrouter.ai/api/v1"
        self.default_model = OPENROUTER_VISION_MODEL_CHAIN[0]
        if not self.api_key:
            # Promoted from WARNING → ERROR. OpenRouter is the secondary
            # provider but it is also the *only* path that produces real AI
            # content when Gemini is unconfigured / quota-exhausted, so a
            # missing key here is operationally significant and deserves to
            # show up at ERROR level in alerting.
            logger.error(
                "openrouter.no_api_key — OPENROUTER_API_KEY is not configured "
                "(or normalized to a placeholder); the OpenRouter fallback "
                "will be skipped and every Gemini failure will produce static "
                "platform content.",
                extra={"event": "openrouter.no_api_key"},
            )

    def _fallback_response(self, reason: str) -> dict[str, Any]:
        fallbacks = get_all_platform_fallbacks()
        return {
            "image_analysis": {
                "description": "Content analysis unavailable",
                "mood": "neutral",
                "key_elements": [],
            },
            "fallback_reason": reason,
            **fallbacks,
        }

    async def analyze_and_generate(self, image_url: str, content_id: str, model: str | None = None) -> dict[str, Any]:
        """
        Drop-in replacement for GeminiService.analyze_and_generate().
        Never raises. Always returns a complete dict.
        """
        if not self.api_key:
            logger.error(
                "openrouter.skip_no_api_key — returning static fallback "
                "because no usable OPENROUTER_API_KEY is configured",
                extra={"event": "openrouter.skip_no_api_key", "content_id": content_id},
            )
            return self._fallback_response("no_api_key")

        fallbacks = get_all_platform_fallbacks()

        def _full_fallback(reason: str) -> dict[str, Any]:
            return self._fallback_response(reason)

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(image_url)
                r.raise_for_status()
                response_content_type = r.headers.get("content-type")
                img_bytes = bytes(r.content)

            mime = _resolve_openrouter_image_mime(image_url, response_content_type)
            if mime is None:
                logger.warning(
                    "openrouter.unsupported_media_type",
                    extra={
                        "content_id": content_id,
                        "content_type": (response_content_type or "").split(";")[0].strip().lower(),
                        "image_url": image_url,
                    },
                )
                return _full_fallback("unsupported_media_type")

            b64_image = base64.b64encode(img_bytes).decode("ascii")
            data_uri = f"data:{mime};base64,{b64_image}"

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ALL_PLATFORMS_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ]

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://reforge.app",
                "X-Title": "ReForge",
                "Content-Type": "application/json",
            }

            models_to_try: tuple[str, ...] = (model,) if model else OPENROUTER_VISION_MODEL_CHAIN
            last_error: str | None = None

            for model_id in models_to_try:
                for use_json_mode in (True, False):
                    body: dict[str, Any] = {
                        "model": model_id,
                        "messages": messages,
                        "max_tokens": 4096,
                        "temperature": 0.7,
                    }
                    if use_json_mode:
                        body["response_format"] = {"type": "json_object"}

                    async with httpx.AsyncClient(timeout=90.0) as http_client:
                        response = await http_client.post(
                            f"{self.base_url}/chat/completions",
                            headers=headers,
                            json=body,
                        )

                    last_error = f"{model_id}:{response.status_code}"

                    if response.status_code != 200:
                        logger.warning(
                            "openrouter.model_attempt_failed",
                            extra={
                                "content_id": content_id,
                                "model": model_id,
                                "json_mode": use_json_mode,
                                "status_code": response.status_code,
                                "preview": response.text[:200],
                            },
                        )
                        continue

                    data = response.json()
                    raw = (
                        (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
                        if isinstance(data, dict)
                        else None
                    )
                    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", str(raw or "")).strip()
                    if not cleaned:
                        logger.warning(
                            "openrouter.empty_body_retry",
                            extra={"content_id": content_id, "model": model_id},
                        )
                        continue
                    try:
                        parsed: dict[str, Any] = json.loads(cleaned)
                    except json.JSONDecodeError:
                        parsed = {}
                    if not isinstance(parsed, dict):
                        parsed = {}
                    if not parsed:
                        logger.warning(
                            "openrouter.empty_json_retry",
                            extra={"content_id": content_id, "model": model_id},
                        )
                        continue

                    for key in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
                        if not parsed.get(key):
                            parsed[key] = fallbacks[key]
                    if not parsed.get("image_analysis"):
                        parsed["image_analysis"] = {
                            "description": "Content analysis unavailable",
                            "mood": "neutral",
                            "key_elements": [],
                        }

                    logger.info(
                        "ai.provider.used provider=openrouter model=%s json_mode=%s "
                        "content_id=%s key_source=%s",
                        model_id,
                        use_json_mode,
                        content_id,
                        self._source or "<unknown>",
                        extra={
                            "event": "ai.provider.used",
                            "provider": "openrouter",
                            "model": model_id,
                            "content_id": content_id,
                            "json_mode": use_json_mode,
                        },
                    )
                    return parsed

            logger.error(
                "openrouter all models failed",
                extra={"content_id": content_id, "last": last_error},
            )
            return _full_fallback("openrouter_error")

        except Exception as exc:
            logger.error(
                "openrouter error; returning fallbacks",
                extra={"content_id": content_id, "exc_type": type(exc).__name__},
            )
            return _full_fallback("openrouter_error")


_openrouter_singleton: OpenRouterService | None = None


def get_openrouter_service() -> OpenRouterService:
    global _openrouter_singleton
    if _openrouter_singleton is None:
        _openrouter_singleton = OpenRouterService()
    return _openrouter_singleton
