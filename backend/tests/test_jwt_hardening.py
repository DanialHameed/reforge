"""B-1 hardening pass: weak/short JWT secret rejection + FERNET startup validation.

The first round of B-1 work refused only an *empty* secret. Production
hardening also rejects:

* known weak placeholders (``change-me``, ``secret``, ``password``,
  ``your-secret-here`` …),
* secrets shorter than 32 chars in non-local environments,
* a missing or syntactically-invalid ``FERNET_KEY`` in non-local environments.

These tests pin the new contract so a future refactor cannot quietly
re-open any of the holes above.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.security import (  # noqa: E402
    _is_valid_fernet_key,
    _is_weak_jwt_secret,
    _jwt_secret_or_die,
    create_access_token,
    validate_security_config_at_startup,
    verify_access_token,
)


_VALID_FERNET = Fernet.generate_key().decode("ascii")
_STRONG_SECRET = "prod-grade-random-secret-1234567890abcdef-and-then-some-more"


def _override(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env: str = "production",
    secret_key: str | None = _STRONG_SECRET,
    jwt_secret_key: str = "change-me",
    fernet_key: str = _VALID_FERNET,
) -> None:
    """Set every Settings field that the security validator inspects."""
    monkeypatch.setattr(settings, "ENV", env)
    monkeypatch.setattr(settings, "SECRET_KEY", secret_key)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", jwt_secret_key)
    monkeypatch.setattr(settings, "FERNET_KEY", fernet_key)


# ---------------------------------------------------------------------------
# _is_weak_jwt_secret
# ---------------------------------------------------------------------------


class TestIsWeakJwtSecret:
    def test_empty_is_weak(self) -> None:
        weak, reason = _is_weak_jwt_secret("", env="production")
        assert weak is True
        assert reason == "not configured"

    @pytest.mark.parametrize(
        "placeholder",
        ["change-me", "ChangeMe", "secret", "PASSWORD", "your-secret-here", "test", "xxx", "default"],
    )
    def test_known_placeholder_rejected(self, placeholder: str) -> None:
        weak, reason = _is_weak_jwt_secret(placeholder, env="production")
        assert weak is True
        assert reason == "known weak placeholder"

    def test_short_secret_rejected_in_production(self) -> None:
        weak, reason = _is_weak_jwt_secret("short-key-123", env="production")
        assert weak is True
        assert "too short" in (reason or "")

    def test_short_secret_allowed_in_local(self) -> None:
        # Local dev should not be blocked by length rules.
        weak, _ = _is_weak_jwt_secret("short", env="local")
        assert weak is False

    def test_long_random_secret_passes(self) -> None:
        weak, reason = _is_weak_jwt_secret(_STRONG_SECRET, env="production")
        assert weak is False
        assert reason is None

    def test_exactly_32_chars_passes(self) -> None:
        weak, _ = _is_weak_jwt_secret("a" * 32, env="production")
        assert weak is False

    def test_31_chars_rejected(self) -> None:
        weak, _ = _is_weak_jwt_secret("a" * 31, env="production")
        assert weak is True


# ---------------------------------------------------------------------------
# _is_valid_fernet_key
# ---------------------------------------------------------------------------


class TestIsValidFernetKey:
    def test_real_fernet_key_passes(self) -> None:
        assert _is_valid_fernet_key(_VALID_FERNET) is True

    def test_empty_rejected(self) -> None:
        assert _is_valid_fernet_key("") is False

    def test_garbage_rejected(self) -> None:
        assert _is_valid_fernet_key("not-a-real-fernet-key") is False

    def test_too_short_b64_rejected(self) -> None:
        # 16 bytes b64 instead of the required 32.
        import base64
        short = base64.urlsafe_b64encode(b"\x00" * 16).decode("ascii")
        assert _is_valid_fernet_key(short) is False


# ---------------------------------------------------------------------------
# _jwt_secret_or_die — runtime defense
# ---------------------------------------------------------------------------


class TestJwtSecretOrDie:
    def test_strong_secret_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _override(monkeypatch)
        assert _jwt_secret_or_die() == _STRONG_SECRET

    def test_weak_placeholder_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _override(monkeypatch, secret_key="change-me", jwt_secret_key="change-me")
        with pytest.raises(RuntimeError) as ei:
            _jwt_secret_or_die()
        assert "unsafe" in str(ei.value).lower()

    def test_short_secret_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", secret_key="short", jwt_secret_key="change-me")
        with pytest.raises(RuntimeError) as ei:
            _jwt_secret_or_die()
        assert "too short" in str(ei.value).lower()

    def test_short_secret_allowed_in_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="local", secret_key="short", jwt_secret_key="change-me")
        assert _jwt_secret_or_die() == "short"


# ---------------------------------------------------------------------------
# create_access_token / verify_access_token — top-level enforcement
# ---------------------------------------------------------------------------


class TestTokenIssuanceRefusesWeakSecret:
    def test_create_token_rejects_short_secret_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", secret_key="short")
        with pytest.raises(RuntimeError):
            create_access_token("user-1", "x@example.com")

    def test_verify_token_rejects_short_secret_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", secret_key="short")
        with pytest.raises(RuntimeError):
            verify_access_token("dummy.jwt.token")


# ---------------------------------------------------------------------------
# validate_security_config_at_startup — boot-time enforcement
# ---------------------------------------------------------------------------


class TestStartupValidator:
    def test_happy_path_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _override(monkeypatch)
        validate_security_config_at_startup()  # must not raise

    def test_short_jwt_secret_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", secret_key="short")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        assert "too short" in str(ei.value).lower()

    def test_known_weak_jwt_secret_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Note: ``settings.jwt_signing_secret`` already pre-filters the
        # exact string ``"change-me"`` (returns ""). The new defensive
        # placeholder layer therefore protects against placeholders Settings
        # does NOT pre-filter — ``secret``, ``password``, ``your-secret-here``,
        # ``test``, etc. We exercise one of those here so the additional
        # layer is actually under test (not just the Settings pre-filter).
        _override(monkeypatch, env="production", secret_key="secret")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        assert "known weak placeholder" in str(ei.value).lower()

    def test_missing_fernet_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", fernet_key="")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        assert "fernet_key" in str(ei.value).lower()

    def test_invalid_fernet_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", fernet_key="not-a-real-fernet-key")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        assert "fernet_key" in str(ei.value).lower()

    def test_placeholder_fernet_raises_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", fernet_key="change-me")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        assert "placeholder" in str(ei.value).lower()

    def test_local_logs_critical_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _override(monkeypatch, env="local", secret_key="change-me", fernet_key="")
        with caplog.at_level(logging.CRITICAL, logger="reforge.security"):
            validate_security_config_at_startup()
        # Both findings logged.
        msgs = [rec.message for rec in caplog.records if rec.levelno == logging.CRITICAL]
        assert any("jwt signing secret" in m.lower() for m in msgs)
        assert any("fernet_key" in m.lower() for m in msgs)

    def test_multiple_failures_combined_into_one_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _override(monkeypatch, env="production", secret_key="short", fernet_key="")
        with pytest.raises(RuntimeError) as ei:
            validate_security_config_at_startup()
        msg = str(ei.value).lower()
        assert "too short" in msg
        assert "fernet_key" in msg


# ---------------------------------------------------------------------------
# token_crypto refuses to encrypt under "change-me"-derived material
# ---------------------------------------------------------------------------


class TestTokenCryptoRefusesPlaceholderEncrypt:
    def test_encrypt_with_no_keys_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import token_crypto

        # Clear every legitimate source.
        monkeypatch.setattr(settings, "FERNET_KEY", "", raising=False)
        monkeypatch.setattr(settings, "SECRET_KEY", "change-me", raising=False)
        monkeypatch.setattr(settings, "JWT_SECRET_KEY", "change-me", raising=False)

        with pytest.raises(RuntimeError) as ei:
            token_crypto.encrypt_token("oauth-refresh-token-payload")
        assert "fernet_key" in str(ei.value).lower()

    def test_encrypt_with_real_fernet_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import token_crypto

        monkeypatch.setattr(settings, "FERNET_KEY", _VALID_FERNET, raising=False)

        ciphertext = token_crypto.encrypt_token("payload")
        assert ciphertext is not None
        decrypted = token_crypto.decrypt_token(ciphertext)
        assert decrypted == "payload"

    def test_legacy_change_me_ciphertext_is_still_decryptable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy rows written before hardening must still decrypt for rotation."""
        from app.services import token_crypto
        from cryptography.fernet import Fernet

        legacy_key = token_crypto._derived_fernet_key_bytes("change-me")
        legacy_cipher = Fernet(legacy_key).encrypt(b"legacy-token").decode("ascii")

        # Operator has now configured a real FERNET_KEY.
        monkeypatch.setattr(settings, "FERNET_KEY", _VALID_FERNET, raising=False)
        monkeypatch.setattr(settings, "SECRET_KEY", _STRONG_SECRET, raising=False)

        plain = token_crypto.try_decrypt_token(legacy_cipher)
        assert plain == "legacy-token"
