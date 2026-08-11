"""
Centralized AI provider configuration & startup validation (B-9 reinforcement).

This module is the single source of truth for:

* Which Gemini key to use (``GEMINI_API_KEY`` > ``GOOGLE_API_KEY``).
* Which OpenRouter key to use (``OPENROUTER_API_KEY``).
* Detecting placeholder / sentinel values that historically slipped through
  silently (e.g. ``change-me``, ``your-api-key-here``, ``replace-me``,
  obvious test strings) — these used to be treated as "configured" by every
  service constructor that did ``os.getenv("KEY", "")``.
* Detecting operator confusion (e.g. ``GEMINI_API_KEY`` and
  ``GOOGLE_API_KEY`` both set to *different* values).
* Reporting at startup which providers are usable, so operators do not first
  notice a misconfiguration via a wave of ``completed_fallback`` content
  items hours after deploy.

Design notes:

* The module performs **no** I/O and **no** network calls — it only reads
  ``Settings`` and ``os.environ``. This keeps ``validate_ai_provider_config_at_startup``
  cheap enough to call in the FastAPI ``lifespan`` and from Celery worker
  bootstrap if we ever wire that up.
* Behavior parity with the existing ``Settings.gemini_api_key`` resolver is
  preserved so the regression suite under ``backend/tests/test_gemini_env_handling.py``
  continues to pass: monkeypatching ``settings.GEMINI_API_KEY`` /
  ``settings.GOOGLE_API_KEY`` continues to drive resolution.
* Every "is the key real?" check goes through ``_normalize_ai_key`` so a single
  rule is enforced for every provider.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

from app.core.config import settings

logger = logging.getLogger("reforge.ai.providers")


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------
#
# These are values that are syntactically a non-empty string but semantically
# "the operator forgot to fill this in". The bundled ``.env.example`` and a
# number of internet tutorials use phrases like ``your-api-key-here`` or
# ``replace-me``; we want those rejected at startup.
#
# Matches are case-insensitive and apply only to the *whole* normalized value
# (not to substrings) — a real key like
# "AIzaSyXXX-yourApi-andSomethingElse" must NOT be rejected just because the
# substring "yourapi" appears inside it.
_PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "change_me",
        "your-api-key-here",
        "your_api_key_here",
        "your-api-key",
        "your_api_key",
        "your-key-here",
        "your_key_here",
        "your-key",
        "your_key",
        "replace-me",
        "replaceme",
        "replace_me",
        "todo",
        "tbd",
        "xxx",
        "xxxx",
        "xxxxx",
        "placeholder",
        "example",
        "example-key",
        "example_key",
        "test",
        "test-key",
        "test_key",
        "<paste-here>",
        "<insert-key>",
    }
)

# Keys are routinely pasted with surrounding quotes by users who copy from
# a JSON config or a docs blob. Stripping these prevents false-negatives.
_QUOTE_CHARS: frozenset[str] = frozenset({'"', "'", "`"})


def _normalize_ai_key(raw: object) -> str:
    """
    Return the canonical form of a configured AI key.

    Rules:
        * ``None`` / non-strings yield ``""``.
        * Surrounding ASCII / smart quotes and whitespace are stripped.
        * Internal whitespace (newlines, tabs) is rejected → ``""``. A real
          API key never legitimately contains whitespace; encountering it
          almost always means the operator pasted a multi-line value.
        * Values whose *normalized* form is in ``_PLACEHOLDER_TOKENS`` are
          rejected → ``""``.
    """
    if not isinstance(raw, str):
        return ""
    v = raw.strip()
    while v and v[0] in _QUOTE_CHARS and v[-1] == v[0] and len(v) >= 2:
        v = v[1:-1].strip()
    if not v:
        return ""
    if any(ch.isspace() for ch in v):
        return ""
    if v.lower() in _PLACEHOLDER_TOKENS:
        return ""
    return v


# ---------------------------------------------------------------------------
# Per-provider resolvers
# ---------------------------------------------------------------------------


def _settings_attr(name: str) -> object:
    """Read a Settings attribute defensively — returns ``None`` if absent."""
    return getattr(settings, name, None)


def _resolve_via_settings_then_env(setting_names: Iterable[str], env_names: Iterable[str]) -> tuple[str, str | None]:
    """
    Try each Settings attribute, then each env-var, returning
    ``(normalized_key_or_empty, source_or_None)``.

    ``source`` is the human-friendly identifier ("GEMINI_API_KEY",
    "env:GOOGLE_API_KEY", …) we use in operator-facing logs so they can see
    *which* environment variable contributed the active value without dumping
    the value itself.
    """
    for name in setting_names:
        normalized = _normalize_ai_key(_settings_attr(name))
        if normalized:
            return normalized, name

    for name in env_names:
        normalized = _normalize_ai_key(os.getenv(name))
        if normalized:
            return normalized, f"env:{name}"

    return "", None


def resolve_gemini_key() -> tuple[str, str | None]:
    """
    Resolve the active Gemini API key.

    Resolution order (matches the legacy ``settings.gemini_api_key`` property
    so the existing test suite is unaffected):
        1. ``settings.GEMINI_API_KEY``
        2. ``settings.GOOGLE_API_KEY``
        3. ``os.environ['GEMINI_API_KEY']``
        4. ``os.environ['GOOGLE_API_KEY']``
    """
    return _resolve_via_settings_then_env(
        setting_names=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        env_names=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )


def resolve_openrouter_key() -> tuple[str, str | None]:
    """
    Resolve the active OpenRouter API key.

    Same shape as ``resolve_gemini_key`` so consumers can swap providers
    without bespoke key-loading logic.
    """
    return _resolve_via_settings_then_env(
        setting_names=("OPENROUTER_API_KEY",),
        env_names=("OPENROUTER_API_KEY",),
    )


# ---------------------------------------------------------------------------
# Provider-state snapshot (used by startup validator + future /health page)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderState:
    """A single provider's configuration state."""

    name: str
    configured: bool
    source: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AIProvidersSnapshot:
    """The full picture for all AI providers as seen at one point in time."""

    gemini: ProviderState
    openrouter: ProviderState

    @property
    def any_configured(self) -> bool:
        return self.gemini.configured or self.openrouter.configured


def _detect_gemini_conflicts() -> tuple[str, ...]:
    """
    Detect operator-confusion cases between ``GEMINI_API_KEY`` and
    ``GOOGLE_API_KEY`` *after* normalization (so both pointing at the same
    real key is silent, but pointing at *different* real keys is flagged).
    """
    g_settings = _normalize_ai_key(_settings_attr("GEMINI_API_KEY"))
    google_settings = _normalize_ai_key(_settings_attr("GOOGLE_API_KEY"))
    g_env = _normalize_ai_key(os.getenv("GEMINI_API_KEY"))
    google_env = _normalize_ai_key(os.getenv("GOOGLE_API_KEY"))

    distinct_keys = {k for k in (g_settings, google_settings, g_env, google_env) if k}
    if len(distinct_keys) > 1:
        return (
            "GEMINI_API_KEY and GOOGLE_API_KEY resolve to different non-empty "
            "values; GEMINI_API_KEY will win. Set only one to avoid surprises.",
        )
    return ()


def describe_ai_providers() -> AIProvidersSnapshot:
    """
    Build an immutable snapshot of every AI provider's current state.

    This function is safe to call at any time — it never raises, never makes
    network calls, and never mutates global state.
    """
    gemini_key, gemini_source = resolve_gemini_key()
    openrouter_key, openrouter_source = resolve_openrouter_key()
    gemini_warnings = _detect_gemini_conflicts()

    return AIProvidersSnapshot(
        gemini=ProviderState(
            name="gemini",
            configured=bool(gemini_key),
            source=gemini_source,
            warnings=gemini_warnings,
        ),
        openrouter=ProviderState(
            name="openrouter",
            configured=bool(openrouter_key),
            source=openrouter_source,
        ),
    )


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def validate_ai_provider_config_at_startup() -> None:
    """
    Validate AI provider configuration during FastAPI ``lifespan``.

    Behavior:

    * ``ENV=local``: log every issue (CRITICAL for "no providers", WARNING
      for individual misses or conflicts) so developers see the problem in
      ``uvicorn`` output but iteration is not blocked.
    * Any non-local ``ENV``: raise ``RuntimeError`` if **no** provider is
      configured. Running production with neither Gemini nor OpenRouter
      means every single piece of generated content will be the static
      fallback — a state that historically only became visible after users
      complained, so we refuse to boot.

    Logged events:
        * ``ai.providers.snapshot``: one INFO line summarizing which provider
          is active (and the source env-var name) — never logs the key value.
        * ``ai.providers.no_providers_configured``: CRITICAL when neither is
          configured.
        * ``ai.providers.partial_configuration``: WARNING when only the
          fallback (OpenRouter) is configured but the primary (Gemini) is
          not — operators may have intended both, and the system runs in a
          permanently-degraded state.
        * ``ai.providers.warning``: one WARNING per detected conflict.
    """
    snapshot = describe_ai_providers()
    env = (settings.ENV or "local").strip().lower()

    logger.info(
        "ai.providers.snapshot env=%s gemini_configured=%s gemini_source=%s "
        "openrouter_configured=%s openrouter_source=%s",
        env,
        snapshot.gemini.configured,
        snapshot.gemini.source or "<none>",
        snapshot.openrouter.configured,
        snapshot.openrouter.source or "<none>",
    )

    for warning in snapshot.gemini.warnings:
        logger.warning("ai.providers.warning provider=gemini detail=%s", warning)
    for warning in snapshot.openrouter.warnings:
        logger.warning("ai.providers.warning provider=openrouter detail=%s", warning)

    if not snapshot.any_configured:
        message = (
            "No AI provider keys are configured. Set GEMINI_API_KEY (preferred), "
            "GOOGLE_API_KEY, or OPENROUTER_API_KEY before serving traffic — "
            "otherwise every content item will be a static fallback."
        )
        logger.critical("ai.providers.no_providers_configured: %s", message)
        if env != "local":
            raise RuntimeError(f"AI misconfiguration ({env}): {message}")
        return

    if not snapshot.gemini.configured and snapshot.openrouter.configured:
        logger.warning(
            "ai.providers.partial_configuration primary=gemini state=missing "
            "fallback=openrouter state=configured "
            "detail=Gemini is the primary AI provider; OpenRouter is the "
            "fallback. Running on the fallback alone is degraded. Set "
            "GEMINI_API_KEY (or GOOGLE_API_KEY) to restore the primary.",
        )

    if snapshot.gemini.configured and not snapshot.openrouter.configured:
        logger.info(
            "ai.providers.partial_configuration primary=gemini state=configured "
            "fallback=openrouter state=missing "
            "detail=Gemini is configured; OpenRouter fallback is not. "
            "If Gemini exhausts quotas, requests will fall back to static "
            "platform content instead of OpenRouter. Set OPENROUTER_API_KEY "
            "to enable the secondary provider.",
        )


__all__ = [
    "AIProvidersSnapshot",
    "ProviderState",
    "describe_ai_providers",
    "resolve_gemini_key",
    "resolve_openrouter_key",
    "validate_ai_provider_config_at_startup",
]
