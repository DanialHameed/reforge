"""B-9 reinforcement: ``app.core.ai_providers`` regression tests.

The previous round of B-9 work fixed Gemini's silent ``os.getenv`` reads
and added a CRITICAL log when no key was configured. This second pass
extends the fix to OpenRouter and adds a proper startup-validation hook so
that misconfigured deployments are detected at boot — not hours later when
operators notice every content item is silently a static fallback.

Tests in this module pin the new contract:

1. ``_normalize_ai_key`` strips whitespace, surrounding quotes, and rejects
   the well-known placeholder values that historically slipped through
   (``change-me``, ``your-api-key-here``, …) while leaving real keys
   untouched.
2. ``resolve_gemini_key`` matches the legacy precedence
   (``GEMINI_API_KEY`` > ``GOOGLE_API_KEY`` > env-var equivalents) and
   returns the *source* identifier so logs can name the active variable.
3. ``resolve_openrouter_key`` mirrors the same shape.
4. ``describe_ai_providers`` produces a snapshot whose ``any_configured``
   flag drives the startup validator.
5. ``describe_ai_providers`` reports an operator-confusion warning when
   ``GEMINI_API_KEY`` and ``GOOGLE_API_KEY`` resolve to *different*
   non-empty values (and stays silent when they point at the same value).
6. ``validate_ai_provider_config_at_startup``:
     * In ``ENV=local``: never raises; logs CRITICAL when no provider is
       configured and WARNING for the partial-configuration case.
     * In ``ENV=production``: raises ``RuntimeError`` when no provider is
       configured.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import ai_providers  # noqa: E402
from app.core.ai_providers import (  # noqa: E402
    _normalize_ai_key,
    describe_ai_providers,
    resolve_gemini_key,
    resolve_openrouter_key,
    validate_ai_provider_config_at_startup,
)
from app.core.config import settings  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every Gemini/OpenRouter source to resolve to empty."""
    for setting_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setattr(settings, setting_name, None, raising=False)
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)


# ---------------------------------------------------------------------------
# _normalize_ai_key
# ---------------------------------------------------------------------------


class TestNormalizeAIKey:
    def test_none_returns_empty(self) -> None:
        assert _normalize_ai_key(None) == ""

    def test_non_string_returns_empty(self) -> None:
        assert _normalize_ai_key(123) == ""
        assert _normalize_ai_key([]) == ""
        assert _normalize_ai_key({}) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert _normalize_ai_key("") == ""
        assert _normalize_ai_key("   ") == ""

    def test_real_key_passes_through(self) -> None:
        # Mirrors the actual Google AI Studio key prefix.
        assert _normalize_ai_key("AIzaSyA-real-key-1234567890") == "AIzaSyA-real-key-1234567890"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _normalize_ai_key("  AIza-real  ") == "AIza-real"

    def test_strips_surrounding_double_quotes(self) -> None:
        assert _normalize_ai_key('"AIza-real"') == "AIza-real"

    def test_strips_surrounding_single_quotes(self) -> None:
        assert _normalize_ai_key("'AIza-real'") == "AIza-real"

    def test_strips_surrounding_backticks(self) -> None:
        assert _normalize_ai_key("`AIza-real`") == "AIza-real"

    def test_strips_quoted_with_extra_whitespace(self) -> None:
        assert _normalize_ai_key('  "AIza-real"  ') == "AIza-real"

    def test_internal_whitespace_rejected(self) -> None:
        # API keys never legitimately contain internal whitespace.
        assert _normalize_ai_key("AIza key") == ""
        assert _normalize_ai_key("AIza\nkey") == ""
        assert _normalize_ai_key("AIza\tkey") == ""

    @pytest.mark.parametrize(
        "placeholder",
        [
            "change-me",
            "ChangeMe",
            "CHANGE_ME",
            "your-api-key-here",
            "your_api_key_here",
            "your-api-key",
            "your_key",
            "replace-me",
            "REPLACE_ME",
            "todo",
            "TBD",
            "xxx",
            "xxxxx",
            "placeholder",
            "example",
            "test",
            "test-key",
            "<paste-here>",
        ],
    )
    def test_placeholder_tokens_rejected(self, placeholder: str) -> None:
        assert _normalize_ai_key(placeholder) == ""

    def test_placeholder_substring_inside_real_key_is_preserved(self) -> None:
        # Substring matching would be a regression — a real key happens to
        # contain "yourapi" should NOT be rejected.
        assert (
            _normalize_ai_key("AIzaSyXXX-yourApi-andSomethingElse")
            == "AIzaSyXXX-yourApi-andSomethingElse"
        )


# ---------------------------------------------------------------------------
# resolve_gemini_key
# ---------------------------------------------------------------------------


class TestResolveGeminiKey:
    def test_no_sources_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        key, source = resolve_gemini_key()
        assert key == ""
        assert source is None

    def test_settings_gemini_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-from-settings", raising=False)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-google-loses", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-env-loses")
        monkeypatch.setenv("GOOGLE_API_KEY", "AIza-google-env-loses")

        key, source = resolve_gemini_key()
        assert key == "AIza-from-settings"
        assert source == "GEMINI_API_KEY"

    def test_settings_google_used_when_gemini_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-google-key", raising=False)

        key, source = resolve_gemini_key()
        assert key == "AIza-google-key"
        assert source == "GOOGLE_API_KEY"

    def test_env_gemini_used_when_settings_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-from-env")

        key, source = resolve_gemini_key()
        assert key == "AIza-from-env"
        assert source == "env:GEMINI_API_KEY"

    def test_env_google_used_when_everything_else_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setenv("GOOGLE_API_KEY", "AIza-from-google-env")

        key, source = resolve_gemini_key()
        assert key == "AIza-from-google-env"
        assert source == "env:GOOGLE_API_KEY"

    def test_placeholder_settings_value_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # B-9 reinforcement: a placeholder in Settings must not "shadow" a
        # real key configured later in the resolution order.
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "your-api-key-here", raising=False)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-real", raising=False)

        key, source = resolve_gemini_key()
        assert key == "AIza-real"
        assert source == "GOOGLE_API_KEY"

    def test_quoted_value_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", '"AIza-quoted"', raising=False)

        key, _ = resolve_gemini_key()
        assert key == "AIza-quoted"


# ---------------------------------------------------------------------------
# resolve_openrouter_key
# ---------------------------------------------------------------------------


class TestResolveOpenrouterKey:
    def test_no_sources_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        key, source = resolve_openrouter_key()
        assert key == ""
        assert source is None

    def test_settings_value_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-real", raising=False)

        key, source = resolve_openrouter_key()
        assert key == "sk-or-v1-real"
        assert source == "OPENROUTER_API_KEY"

    def test_env_value_used_when_settings_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env")

        key, source = resolve_openrouter_key()
        assert key == "sk-or-v1-from-env"
        assert source == "env:OPENROUTER_API_KEY"

    def test_placeholder_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Operators frequently paste ``OPENROUTER_API_KEY=your-key-here``
        # from the docs and never come back to fix it. The resolver must
        # reject this so the system doesn't silently treat the fallback as
        # available.
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "your-key-here", raising=False)

        key, source = resolve_openrouter_key()
        assert key == ""
        assert source is None


# ---------------------------------------------------------------------------
# describe_ai_providers / conflict detection
# ---------------------------------------------------------------------------


class TestDescribeAIProviders:
    def test_snapshot_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        snap = describe_ai_providers()
        assert snap.gemini.configured is False
        assert snap.openrouter.configured is False
        assert snap.any_configured is False

    def test_snapshot_when_gemini_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-x", raising=False)
        snap = describe_ai_providers()
        assert snap.gemini.configured is True
        assert snap.gemini.source == "GEMINI_API_KEY"
        assert snap.openrouter.configured is False
        assert snap.any_configured is True

    def test_snapshot_when_openrouter_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-x", raising=False)
        snap = describe_ai_providers()
        assert snap.gemini.configured is False
        assert snap.openrouter.configured is True
        assert snap.openrouter.source == "OPENROUTER_API_KEY"
        assert snap.any_configured is True

    def test_no_conflict_warning_when_both_point_to_same_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-same", raising=False)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-same", raising=False)
        snap = describe_ai_providers()
        assert snap.gemini.warnings == ()

    def test_conflict_warning_when_keys_differ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-one", raising=False)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-two", raising=False)
        snap = describe_ai_providers()
        assert any("different non-empty values" in w for w in snap.gemini.warnings)


# ---------------------------------------------------------------------------
# validate_ai_provider_config_at_startup
# ---------------------------------------------------------------------------


class TestStartupValidator:
    def test_local_no_providers_logs_critical_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "local", raising=False)

        with caplog.at_level(logging.CRITICAL, logger="reforge.ai.providers"):
            validate_ai_provider_config_at_startup()

        assert any(
            rec.levelno == logging.CRITICAL
            and "no_providers_configured" in rec.message.lower()
            for rec in caplog.records
        )

    def test_production_no_providers_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "production", raising=False)

        with pytest.raises(RuntimeError) as ei:
            validate_ai_provider_config_at_startup()
        assert "AI misconfiguration" in str(ei.value)
        assert "production" in str(ei.value)

    def test_production_with_only_openrouter_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "production", raising=False)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-x", raising=False)

        with caplog.at_level(logging.WARNING, logger="reforge.ai.providers"):
            validate_ai_provider_config_at_startup()

        # Boot succeeded but operators get a WARNING that the primary is missing.
        assert any(
            rec.levelno == logging.WARNING
            and "partial_configuration" in rec.message.lower()
            and "primary=gemini" in rec.message.lower()
            for rec in caplog.records
        )

    def test_production_with_gemini_only_logs_partial_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "production", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-x", raising=False)

        with caplog.at_level(logging.INFO, logger="reforge.ai.providers"):
            validate_ai_provider_config_at_startup()

        assert any(
            "partial_configuration" in rec.message.lower()
            and "fallback=openrouter" in rec.message.lower()
            for rec in caplog.records
        )

    def test_production_with_both_providers_does_not_raise_or_warn(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "production", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-x", raising=False)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-x", raising=False)

        with caplog.at_level(logging.INFO, logger="reforge.ai.providers"):
            validate_ai_provider_config_at_startup()

        # Snapshot line is still emitted (operational telemetry).
        assert any("ai.providers.snapshot" in rec.message for rec in caplog.records)
        # But neither CRITICAL nor partial_configuration warnings appear.
        assert not any(rec.levelno >= logging.WARNING for rec in caplog.records)

    def test_conflict_warning_emitted_when_keys_disagree(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _scrub_env(monkeypatch)
        monkeypatch.setattr(settings, "ENV", "production", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "AIza-one", raising=False)
        monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIza-two", raising=False)

        with caplog.at_level(logging.WARNING, logger="reforge.ai.providers"):
            validate_ai_provider_config_at_startup()

        assert any(
            rec.levelno == logging.WARNING
            and "ai.providers.warning" in rec.message
            and "different non-empty values" in rec.message
            for rec in caplog.records
        )

    def test_module_exports(self) -> None:
        # Public API surface — protect against accidental rename.
        assert hasattr(ai_providers, "validate_ai_provider_config_at_startup")
        assert hasattr(ai_providers, "describe_ai_providers")
        assert hasattr(ai_providers, "resolve_gemini_key")
        assert hasattr(ai_providers, "resolve_openrouter_key")
