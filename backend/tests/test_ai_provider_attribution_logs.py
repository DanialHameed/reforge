"""B-9 reinforcement: provider attribution logging.

After the second pass of B-9 changes, every successful AI response and
every static-fallback exit emits a single, well-shaped log line of the
form::

    ai.provider.used provider=<gemini|openrouter|static_fallback> ...

Operators rely on this attribution to (a) tell at a glance whether content
is real-AI or a fallback, (b) count silent "fake success" events, and
(c) confirm that a freshly-deployed environment is *actually* using the
intended provider. These tests pin the log shape so a future refactor
cannot quietly drop the line.
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


def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None, raising=False)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", None, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


class TestGeminiSuccessLogging:
    @pytest.mark.asyncio
    async def test_successful_gemini_call_logs_provider_used(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A successful Gemini call must emit ``ai.provider.used provider=gemini``."""
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-real-test-key", raising=False)

        breaker_mock = mock.MagicMock()
        breaker_mock.is_available.return_value = True

        # Stub the per-model worker to avoid touching `genai.Client`.
        async def _fake_to_thread(fn, *args, **kwargs):
            return {
                "image_analysis": {"description": "x", "mood": "neutral", "key_elements": []},
                "instagram": {"caption": "ig", "hashtags": [], "story_text": ""},
                "twitter": {"tweet": "tw", "thread": []},
                "linkedin": {"post": "li", "hashtags": []},
                "facebook": {"post": "fb", "hashtags": []},
                "youtube": {"title": "yt", "description": "", "tags": []},
            }

        with (
            mock.patch(
                "app.services.gemini_service.get_gemini_circuit_breaker",
                return_value=breaker_mock,
            ),
            mock.patch("app.services.gemini_service.asyncio.to_thread", new=_fake_to_thread),
        ):
            svc = GeminiService()
            with caplog.at_level(logging.INFO, logger="app.services.gemini_service"):
                result = await svc.analyze_and_generate("https://x/y.jpg", "cid-success")

        assert "fallback_reason" not in result, "successful path must not look like a fallback"
        assert any(
            rec.levelno == logging.INFO
            and "ai.provider.used" in rec.message
            and "provider=gemini" in rec.message
            and "cid-success" in rec.message
            for rec in caplog.records
        ), "expected ai.provider.used provider=gemini log line on success"


class TestStaticFallbackLogging:
    @pytest.mark.asyncio
    async def test_static_fallback_logs_provider_used_with_reason(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The full static-fallback exit must log ``provider=static_fallback`` + reason."""
        _scrub_env(monkeypatch)
        # Force the circuit breaker open so analyze_and_generate goes
        # straight to ``_full_fallback("circuit_breaker_open")``.

        class _OpenBreaker:
            def is_available(self) -> bool:
                return False

            def record_success(self) -> None:  # pragma: no cover
                return None

            def record_failure(self) -> None:  # pragma: no cover
                return None

        with mock.patch(
            "app.services.gemini_service.get_gemini_circuit_breaker",
            return_value=_OpenBreaker(),
        ):
            svc = GeminiService()
            with caplog.at_level(logging.WARNING, logger="app.services.gemini_service"):
                result = await svc.analyze_and_generate("https://x/y.jpg", "cid-fb")

        assert result.get("fallback_reason") == "circuit_breaker_open"
        assert any(
            rec.levelno == logging.WARNING
            and "ai.provider.used" in rec.message
            and "provider=static_fallback" in rec.message
            and "circuit_breaker_open" in rec.message
            for rec in caplog.records
        ), "expected ai.provider.used provider=static_fallback log with reason"
