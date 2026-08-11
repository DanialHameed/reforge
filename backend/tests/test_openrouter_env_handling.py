"""B-9 reinforcement: ``OpenRouterService`` env-key handling.

The previous code read ``os.getenv("OPENROUTER_API_KEY", "")`` directly
inside ``OpenRouterService.__init__``, bypassing the Settings layer
entirely. As a result:

* Placeholder values like ``OPENROUTER_API_KEY=your-key-here`` were treated
  as "configured" — the service would call OpenRouter, hit 401, and report
  ``openrouter_error`` instead of ``no_api_key``.
* Values exported in environments where ``Settings`` already loaded (e.g.
  Celery workers re-using a stale Settings instance) silently disagreed
  with the value Gemini saw.
* The "missing key" log was a WARNING, which never paged anyone.

These tests pin the new contract:

1. Construction reads through ``app.core.ai_providers.resolve_openrouter_key``.
2. The constructor records the *source* of the key for log attribution.
3. Construction with no key emits an ERROR log (not WARNING).
4. ``analyze_and_generate`` short-circuits when no key is configured —
   it does NOT iterate the model chain (and therefore does NOT make any
   HTTP requests) and goes directly to the static fallback.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.openrouter_service import OpenRouterService  # noqa: E402


def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


class TestOpenRouterConstruction:
    def test_uses_settings_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-real", raising=False)

        svc = OpenRouterService()
        assert svc.api_key == "sk-or-v1-real"
        assert svc._source == "OPENROUTER_API_KEY"

    def test_uses_env_value_when_settings_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env")

        svc = OpenRouterService()
        assert svc.api_key == "sk-or-v1-from-env"
        assert svc._source == "env:OPENROUTER_API_KEY"

    def test_placeholder_rejected_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(
            settings, "OPENROUTER_API_KEY", "your-key-here", raising=False
        )

        svc = OpenRouterService()
        assert svc.api_key == ""
        assert svc._source is None

    def test_no_key_logs_at_error_level(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)

        with caplog.at_level(logging.ERROR, logger="app.services.openrouter_service"):
            OpenRouterService()

        assert any(
            rec.levelno == logging.ERROR
            and "openrouter.no_api_key" in rec.message.lower()
            for rec in caplog.records
        ), "expected an ERROR log line about the missing OpenRouter API key"

    def test_quoted_value_is_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(
            settings, "OPENROUTER_API_KEY", '"sk-or-v1-quoted"', raising=False
        )

        svc = OpenRouterService()
        assert svc.api_key == "sk-or-v1-quoted"


class TestOpenRouterAnalyzeAndGenerate:
    @pytest.mark.asyncio
    async def test_no_key_short_circuits_and_makes_no_http_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The short-circuit is the safety net the static-fallback path relies on."""
        _scrub_env(monkeypatch)

        client_mock = mock.MagicMock()
        with mock.patch(
            "app.services.openrouter_service.httpx.AsyncClient", new=client_mock
        ):
            svc = OpenRouterService()
            result = await svc.analyze_and_generate(
                "https://example.com/img.jpg", "cid-1"
            )

        # No HTTP client was ever constructed.
        client_mock.assert_not_called()
        assert isinstance(result, dict)
        assert result.get("fallback_reason") == "no_api_key"
        for k in ("instagram", "twitter", "linkedin", "facebook", "youtube"):
            assert k in result

    @pytest.mark.asyncio
    async def test_no_key_logs_skip_at_error_level(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        # The constructor itself logs ERROR; clear and re-capture only the
        # call-site log so we test the skip path independently.
        svc = OpenRouterService()
        caplog.clear()

        with caplog.at_level(logging.ERROR, logger="app.services.openrouter_service"):
            result = await svc.analyze_and_generate("https://x/y.jpg", "cid-2")

        assert result.get("fallback_reason") == "no_api_key"
        assert any(
            rec.levelno == logging.ERROR
            and "openrouter.skip_no_api_key" in rec.message
            for rec in caplog.records
        )
