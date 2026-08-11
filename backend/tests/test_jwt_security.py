"""Regression tests for the B-1 JWT signing-secret vulnerability.

Before the fix, ``app.core.security`` read ``settings.JWT_SECRET_KEY``
directly. Because the bundled ``.env`` only configures ``SECRET_KEY`` and
``JWT_SECRET_KEY`` defaulted to the literal string ``"change-me"``, every
issued access token was signed with a publicly-known placeholder. These
tests pin down the new behavior:

* Tokens are signed with the canonical resolved secret
  (``settings.jwt_signing_secret``) which prefers ``JWT_SECRET_KEY`` when
  explicitly set and otherwise falls back to ``SECRET_KEY``.
* Issuing or verifying a token while the resolved secret is empty or the
  default placeholder is a hard error.
* The startup validator fails fast in non-local environments.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from jose import jwt

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402  (path-mutation needed first)
from app.core.security import (  # noqa: E402
    _jwt_secret_or_die,
    create_access_token,
    validate_security_config_at_startup,
    verify_access_token,
)


def _set_secrets(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret_key: str | None,
    jwt_secret_key: str = "change-me",
) -> None:
    """Override the in-memory settings singleton for a single test only.

    ``monkeypatch.setattr`` reverts the values automatically when the test
    exits, so the singleton is restored to whatever the real environment
    provided at import time.
    """
    monkeypatch.setattr(settings, "SECRET_KEY", secret_key)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", jwt_secret_key)


def test_jwt_uses_secret_key_when_only_secret_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundled `.env` only sets SECRET_KEY — JWT must sign with it."""
    _set_secrets(
        monkeypatch,
        secret_key="test-secret-key-value-1234567890abcdef",
        jwt_secret_key="change-me",
    )

    token = create_access_token("user-1", "user1@example.com")
    decoded = jwt.decode(
        token,
        "test-secret-key-value-1234567890abcdef",
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert decoded["sub"] == "user-1"
    assert decoded["email"] == "user1@example.com"

    # Sanity: the broken default placeholder must NOT verify the token.
    with pytest.raises(Exception):
        jwt.decode(token, "change-me", algorithms=[settings.JWT_ALGORITHM])


def test_jwt_prefers_jwt_secret_key_when_explicitly_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compat: deployments that only set JWT_SECRET_KEY keep working."""
    _set_secrets(
        monkeypatch,
        secret_key=None,
        jwt_secret_key="legacy-jwt-secret-1234567890abcdef-1234567890",
    )

    token = create_access_token("user-2", "user2@example.com")
    decoded = jwt.decode(
        token,
        "legacy-jwt-secret-1234567890abcdef-1234567890",
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert decoded["sub"] == "user-2"


def test_jwt_secret_key_takes_precedence_when_both_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compat: if JWT_SECRET_KEY is explicitly set we keep using it."""
    _set_secrets(
        monkeypatch,
        secret_key="should-not-be-used-1234567890",
        jwt_secret_key="real-jwt-secret-1234567890abcdef",
    )

    token = create_access_token("user-3", "user3@example.com")
    decoded = jwt.decode(
        token,
        "real-jwt-secret-1234567890abcdef",
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert decoded["sub"] == "user-3"


def test_create_access_token_refuses_default_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth — refuse to issue tokens signed with `change-me`."""
    _set_secrets(monkeypatch, secret_key=None, jwt_secret_key="change-me")

    with pytest.raises(RuntimeError) as exc:
        create_access_token("user-4", "user4@example.com")
    assert "secret" in str(exc.value).lower()

    with pytest.raises(RuntimeError):
        _jwt_secret_or_die()


def test_verify_access_token_refuses_default_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification must also refuse the default placeholder, not just signing."""
    _set_secrets(monkeypatch, secret_key=None, jwt_secret_key="change-me")
    forged = jwt.encode(
        {"sub": "attacker", "email": "x@x"},
        "change-me",
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(RuntimeError):
        verify_access_token(forged)


def test_create_then_verify_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokens minted by `create_access_token` must verify via `verify_access_token`."""
    _set_secrets(
        monkeypatch,
        secret_key="round-trip-secret-1234567890abcdef",
        jwt_secret_key="change-me",
    )

    token = create_access_token("user-5", "user5@example.com")
    payload = verify_access_token(token)
    assert payload["sub"] == "user-5"
    assert payload["email"] == "user5@example.com"


def test_validate_at_startup_warns_in_local_env(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Local dev must surface the misconfiguration but not block the app."""
    _set_secrets(monkeypatch, secret_key=None, jwt_secret_key="change-me")
    monkeypatch.setattr(settings, "ENV", "local")

    with caplog.at_level(logging.CRITICAL, logger="reforge.security"):
        validate_security_config_at_startup()

    assert any(
        "secret" in rec.message.lower() for rec in caplog.records
    ), "expected a CRITICAL log line about the missing JWT secret"


def test_validate_at_startup_raises_in_non_local_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production / staging / any non-local env must refuse to boot."""
    _set_secrets(monkeypatch, secret_key=None, jwt_secret_key="change-me")
    monkeypatch.setattr(settings, "ENV", "production")

    with pytest.raises(RuntimeError) as exc:
        validate_security_config_at_startup()
    assert "secret" in str(exc.value).lower()


def test_validate_at_startup_passes_when_secret_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a properly configured secret leaves the validator silent."""
    _set_secrets(
        monkeypatch,
        secret_key="prod-grade-random-secret-1234567890abcdef",
        jwt_secret_key="change-me",
    )
    monkeypatch.setattr(settings, "ENV", "production")
    validate_security_config_at_startup()  # must not raise
