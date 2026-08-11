"""
# Run: pytest tests/test_gemini_pipeline.py -v
# Run async tests: pytest tests/test_gemini_pipeline.py -v --asyncio-mode=auto
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

import pytest
import pytest_asyncio  # noqa: F401  (required by project; marker used below)

# Ensure `app` package is importable when running pytest from repo root.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.fallbacks import get_all_platform_fallbacks
from app.core.retry_engine import GeminiRetryEngine
from app.services.gemini_service import GeminiService
from app.services.openrouter_service import OpenRouterService


def test_circuit_breaker_trips_after_threshold():
    """Circuit breaker should open after N consecutive failures."""
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_available() is False


def test_circuit_breaker_recovers_after_timeout():
    """OPEN should transition to HALF_OPEN after recovery timeout elapses."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    asyncio.run(asyncio.sleep(0.02))
    assert cb.state == CircuitState.HALF_OPEN


def test_circuit_breaker_closes_after_successes():
    """HALF_OPEN should close after required number of successes."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01, success_threshold=1)
    cb.record_failure()
    asyncio.run(asyncio.sleep(0.02))
    _ = cb.state  # trigger OPEN -> HALF_OPEN transition
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_retry_engine_retries_on_transient_error():
    """Retry engine should retry transient errors up to MAX_ATTEMPTS."""
    fn = mock.MagicMock(side_effect=[Exception("server error"), Exception("server error"), {"ok": True}])
    result = GeminiRetryEngine.execute(fn)
    assert fn.call_count == 3
    assert result == {"ok": True}


def test_retry_engine_skips_retry_on_terminal_error():
    """Retry engine should not retry terminal errors like invalid API key."""
    fn = mock.MagicMock(side_effect=Exception("invalid api key"))
    with pytest.raises(Exception):
        GeminiRetryEngine.execute(fn)
    assert fn.call_count == 1


def test_fallbacks_have_all_platforms():
    """Fallback payloads must include all platform keys and minimal non-empty content."""
    fb = get_all_platform_fallbacks()
    for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
        assert k in fb
    assert isinstance(fb["instagram"]["caption"], str) and fb["instagram"]["caption"].strip()
    assert isinstance(fb["twitter"]["thread"], list) and len(fb["twitter"]["thread"]) > 0
    assert isinstance(fb["youtube"]["tags"], list) and len(fb["youtube"]["tags"]) > 0


@pytest.mark.asyncio
async def test_gemini_service_returns_fallback_on_circuit_open():
    """GeminiService should return full fallback immediately when circuit is open."""

    class _AlwaysOpenBreaker:
        def is_available(self) -> bool:
            return False

        def record_success(self) -> None:  # pragma: no cover
            return None

        def record_failure(self) -> None:  # pragma: no cover
            return None

    with mock.patch("app.services.gemini_service.get_gemini_circuit_breaker", return_value=_AlwaysOpenBreaker()):
        svc = GeminiService()
        out = await svc.analyze_and_generate("http://fake.url/img.jpg", "test-id")

    assert "fallback_reason" in out
    for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
        assert k in out


@pytest.mark.asyncio
async def test_gemini_service_never_raises():
    """GeminiService must never raise, even if Gemini consistently errors."""

    class _DummyOpenRouter(OpenRouterService):
        async def analyze_and_generate(self, image_url: str, content_id: str, model: str = None):  # type: ignore[override]
            fb = get_all_platform_fallbacks()
            return {
                "image_analysis": {"description": "Content analysis unavailable", "mood": "neutral", "key_elements": []},
                "fallback_reason": "openrouter_error",
                **fb,
            }

    mock_client = mock.MagicMock()
    mock_client.models.generate_content.side_effect = Exception("quota exceeded")
    with (
        mock.patch("app.services.gemini_service.genai.Client", return_value=mock_client),
        mock.patch("app.services.openrouter_service.get_openrouter_service", return_value=_DummyOpenRouter()),
        mock.patch.object(GeminiService, "_fetch_media", return_value=(b"fake", "image/jpeg")),
    ):
        svc = GeminiService()
        out = await svc.analyze_and_generate("http://fake.url/img.jpg", "test-id")

    assert isinstance(out, dict)
    for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
        assert k in out


def test_json_parse_recovery():
    """Malformed fenced JSON should parse and inject missing platforms as fallbacks."""
    svc = GeminiService()
    out = svc._parse_and_recover(  # pylint: disable=protected-access
        "```json\n{\"instagram\": {\"caption\": \"test\", \"hashtags\": [], \"story_text\": \"\"}}\n```",
        "test-id",
    )
    assert out["instagram"]["caption"] == "test"
    assert "twitter" in out


def test_partial_json_recovery():
    """Partial JSON missing a platform key should preserve provided keys and inject missing with fallback."""
    svc = GeminiService()
    raw = (
        "{"
        "\"instagram\": {\"caption\": \"ig\", \"hashtags\": [], \"story_text\": \"\"},"
        "\"twitter\": {\"tweet\": \"tw\", \"thread\": []},"
        "\"linkedin\": {\"post\": \"li\", \"hashtags\": []},"
        "\"facebook\": {\"post\": \"fb\", \"hashtags\": []}"
        "}"
    )
    out = svc._parse_and_recover(raw, "test-id")  # pylint: disable=protected-access
    fb = get_all_platform_fallbacks()
    assert out["youtube"] == fb["youtube"]

