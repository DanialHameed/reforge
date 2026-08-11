from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


logger = logging.getLogger("reforge.security")


# Derivations from these placeholder values are NEVER used to encrypt new
# OAuth tokens. Reading legacy ciphertexts that were unfortunately written
# with this fallback is still allowed (so we can decrypt and rotate them);
# encrypting new ones is refused outright.
_PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {"", "change-me", "changeme", "change_me", "secret", "password"}
)


def _derived_fernet_key_bytes(secret_material: str) -> bytes:
    """Derive a Fernet-format key from arbitrary secret material."""
    digest = hashlib.sha256(
        secret_material.encode("utf-8") + b"|reforge-oauth-token-fernet-v1"
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _safe_secret_material() -> str | None:
    """
    Pick the secret material to derive a Fernet key from when no explicit
    FERNET_KEY is configured.

    Returns ``None`` if every candidate is empty or a known placeholder. The
    caller is expected to refuse to encrypt new ciphertexts in that case.
    """
    for candidate in (settings.secret_key, settings.JWT_SECRET_KEY):
        v = (candidate or "").strip()
        if v and v.lower() not in _PLACEHOLDER_SECRETS:
            return v
    return None


def _fernet_key_candidates() -> list[bytes]:
    """
    Ordered list of Fernet keys to try when decrypting (read path).

    Rows may have been written when only the derived-from-SECRET_KEY key was
    active, then the operator added FERNET_KEY (or rotated), causing a
    single-key decrypt to fail with InvalidSignature on /platforms/status.
    The decrypt path therefore tries every plausibly-active key, including
    legacy placeholder-derived ones, so existing rows can be read and
    re-encrypted under the current key.
    """
    keys: list[bytes] = []
    seen: set[bytes] = set()

    raw = (settings.FERNET_KEY or "").strip()
    if raw and raw.lower() not in _PLACEHOLDER_SECRETS:
        try:
            Fernet(raw.encode("utf-8"))
            kb = raw.encode("utf-8")
            if kb not in seen:
                seen.add(kb)
                keys.append(kb)
        except ValueError:
            pass

    safe_material = _safe_secret_material()
    if safe_material is not None:
        kb = _derived_fernet_key_bytes(safe_material)
        if kb not in seen:
            seen.add(kb)
            keys.append(kb)

    # B-1 hardening: legacy ciphertexts written before this hardening pass
    # may have been encrypted with the literal "change-me" derivation.
    # Allow READ (so we can decrypt and rotate) but never WRITE — see
    # ``_fernet_key_bytes`` below.
    legacy_kb = _derived_fernet_key_bytes("change-me")
    if legacy_kb not in seen:
        keys.append(legacy_kb)

    return keys


def _fernet_key_bytes() -> bytes:
    """
    Canonical encryption key for **writing** new ciphertexts.

    Resolution order:
        1. explicit ``FERNET_KEY`` if it parses as a valid Fernet key.
        2. SHA-256-derivation from ``SECRET_KEY``/``JWT_SECRET_KEY`` IFF
           that material is not a placeholder.

    Raises ``RuntimeError`` if neither path produces a usable key. This
    refusal replaces the previous silent fallback to a key derived from the
    literal string ``"change-me"`` — a fallback which made every freshly
    encrypted OAuth token effectively public, regardless of how secure the
    operator believed the deployment to be.
    """
    raw = (settings.FERNET_KEY or "").strip()
    if raw and raw.lower() not in _PLACEHOLDER_SECRETS:
        try:
            Fernet(raw.encode("utf-8"))
            return raw.encode("utf-8")
        except ValueError:
            logger.error(
                "token_crypto.invalid_fernet_key: FERNET_KEY is set but is "
                "not a valid Fernet key; falling back to derived material."
            )

    safe_material = _safe_secret_material()
    if safe_material is not None:
        return _derived_fernet_key_bytes(safe_material)

    raise RuntimeError(
        "Cannot encrypt OAuth token: neither FERNET_KEY nor a non-placeholder "
        "SECRET_KEY/JWT_SECRET_KEY is configured. Set FERNET_KEY to a value "
        "generated via: python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )


def try_decrypt_token(token_encrypted: str | None) -> str | None:
    """Decrypt with any known Fernet material; None if ciphertext missing or unreadable."""
    if not token_encrypted:
        return None
    data = token_encrypted.encode("utf-8")
    for kb in _fernet_key_candidates():
        try:
            return Fernet(kb).decrypt(data).decode("utf-8")
        except InvalidToken:
            continue
    return None


def decrypt_token(token_encrypted: str | None) -> str | None:
    """Decrypt OAuth token ciphertext; raises if present but unreadable under all keys."""
    if not token_encrypted:
        return None
    plain = try_decrypt_token(token_encrypted)
    if plain is None:
        raise ValueError("Invalid encrypted token (check FERNET_KEY).")
    return plain


def encrypt_token(token_plain: str | None) -> str | None:
    if not token_plain:
        return None
    f = Fernet(_fernet_key_bytes())
    return f.encrypt(token_plain.encode("utf-8")).decode("utf-8")
