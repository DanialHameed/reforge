"""B-9 regression tests: GeminiService env-key handling.

The previous code read ``os.getenv("GEMINI_API_KEY", "")`` directly inside
``GeminiService.__init__`` and emitted only a WARNING when the value was
empty. Two real failure modes followed:

* ``GOOGLE_API_KEY`` was the configured variable in some environments — the
  service ignored it and silently used an empty key.
* A misconfigured deployment ran in a degraded "always fallback" mode that
  was invisible to operators.

These tests pin down the new behavior:

1. Construction reads from the canonical resolver ``settings.gemini_api_key``
   (so future env-var aliases / fallbacks defined in ``Settings`` propagate
   automatically).
2. ``GOOGLE_API_KEY`` is honored when ``GEMINI_API_KEY`` is absent.
3. ``GEMINI_API_KEY`` takes precedence when both are set.
4. Construction with no key emits a CRITICAL log (not WARNING).
5. ``analyze_and_generate`` short-circuits when no key is configured —
   it does NOT iterate the model chain (and therefore does NOT consume the
   circuit breaker budget) and goes directly to the OpenRouter fallback.
6. ``analyze_and_generate`` STILL never raises in the no-key path.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.gemini_service import GeminiService  # noqa: E402


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``settings.gemini_api_key`` to resolve to empty.

    The settings property checks (in order):
      1. ``self.GEMINI_API_KEY``
      2. ``self.GOOGLE_API_KEY``
      3. ``os.getenv("GEMINI_API_KEY")``
      4. ``os.getenv("GOOGLE_API_KEY")``
    All four must be empty to exercise the no-key path.
    """
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Resolver paths
# ---------------------------------------------------------------------------


def test_construction_uses_canonical_settings_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GeminiService`` must read ``settings.gemini_api_key`` (not raw env)."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "from-settings-12345")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)

    svc = GeminiService()
    assert svc._api_key == "from-settings-12345"


def test_google_api_key_is_honored_when_gemini_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-9 backward compat: the legacy ``GOOGLE_API_KEY`` must keep working."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google-fallback-key-67890")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    svc = GeminiService()
    assert svc._api_key == "google-fallback-key-67890"


def test_gemini_api_key_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both vars are set, ``GEMINI_API_KEY`` wins (matches Settings)."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "primary-gemini-key")
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "google-fallback-ignored")

    svc = GeminiService()
    assert svc._api_key == "primary-gemini-key"


# ---------------------------------------------------------------------------
# No-key behavior: CRITICAL log, no Gemini calls, OpenRouter shortcut, no raise
# ---------------------------------------------------------------------------


def test_construction_with_no_key_logs_critical(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A misconfigured environment must surface as CRITICAL, not WARNING.

    Operators rely on log severity for alerting; the previous WARNING level
    let the service run permanently in degraded mode without paging anyone.
    """
    _clear_env(monkeypatch)

    with caplog.at_level(logging.CRITICAL, logger="app.services.gemini_service"):
        svc = GeminiService()

    assert svc._api_key == ""
    assert any(
        rec.levelno == logging.CRITICAL and "no_api_key" in rec.message.lower()
        for rec in caplog.records
    ), "expected a CRITICAL log line about the missing Gemini API key"


@pytest.mark.asyncio
async def test_analyze_and_generate_skips_gemini_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key -> never construct ``genai.Client``, go straight to OpenRouter.

    This is the operational property B-9 unlocks: a misconfigured key no
    longer wastes the model chain budget producing N exceptions, and the
    circuit breaker stays untouched (so the breaker state reflects only real
    upstream failures).
    """
    _clear_env(monkeypatch)

    fallback_payload = {
        "image_analysis": {
            "description": "stub",
            "mood": "neutral",
            "key_elements": [],
        },
        "instagram": {"caption": "ig", "hashtags": [], "story_text": ""},
        "twitter": {"tweet": "tw", "thread": []},
        "linkedin": {"post": "li", "hashtags": []},
        "facebook": {"post": "fb", "hashtags": []},
        "youtube": {"title": "yt", "description": "", "tags": []},
    }

    class _StubOpenRouter:
        async def analyze_and_generate(self, image_url, content_id):  # type: ignore[no-untyped-def]
            return fallback_payload

    genai_client_mock = mock.MagicMock()
    breaker_mock = mock.MagicMock()
    breaker_mock.is_available.return_value = True

    with (
        mock.patch(
            "app.services.gemini_service.genai.Client", new=genai_client_mock
        ),
        mock.patch(
            "app.services.gemini_service.get_gemini_circuit_breaker",
            return_value=breaker_mock,
        ),
        mock.patch(
            "app.services.openrouter_service.get_openrouter_service",
            return_value=_StubOpenRouter(),
        ),
    ):
        svc = GeminiService()
        result = await svc.analyze_and_generate("http://fake/img.jpg", "cid-1")

    assert result is fallback_payload
    genai_client_mock.assert_not_called()
    breaker_mock.record_failure.assert_not_called()
    breaker_mock.record_success.assert_not_called()


@pytest.mark.asyncio
async def test_no_key_path_returns_static_fallback_when_openrouter_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-key + failed OpenRouter must still never raise; returns full fallback."""
    _clear_env(monkeypatch)

    class _BoomOpenRouter:
        async def analyze_and_generate(self, image_url, content_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("openrouter exploded")

    breaker_mock = mock.MagicMock()
    breaker_mock.is_available.return_value = True

    with (
        mock.patch(
            "app.services.gemini_service.get_gemini_circuit_breaker",
            return_value=breaker_mock,
        ),
        mock.patch(
            "app.services.openrouter_service.get_openrouter_service",
            return_value=_BoomOpenRouter(),
        ),
    ):
        svc = GeminiService()
        result = await svc.analyze_and_generate("http://fake/img.jpg", "cid-2")

    assert isinstance(result, dict)
    assert result.get("fallback_reason") == "no_api_key"
    for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
        assert k in result
