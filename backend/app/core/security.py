from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.auth_models import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

logger = logging.getLogger("reforge.security")


# Minimum acceptable length for JWT signing secret in non-local environments.
# 32 chars ≈ 192 bits of entropy if random; well under the SHA-256 output
# the JOSE library uses internally, but enough to make brute-force forgery
# economically infeasible. Cryptography best practice for HMAC keys.
_JWT_SECRET_MIN_LEN_PROD = 32

# Strings that look like a secret but are universally-known / publicly
# reachable. Rejected even if they meet the length minimum (defense in
# depth — these were copied from a tutorial, not generated). Matches are
# case-insensitive and apply to the *whole* normalized value, not substrings.
_KNOWN_WEAK_SECRETS: frozenset[str] = frozenset(
    {
        "change-me",
        "changeme",
        "change_me",
        "secret",
        "secret-key",
        "secret_key",
        "password",
        "your-secret-here",
        "your_secret_here",
        "your-secret",
        "your_secret",
        "your-secret-key",
        "replace-me",
        "replaceme",
        "test",
        "test-secret",
        "todo",
        "tbd",
        "xxx",
        "placeholder",
        "example",
        "default",
        # The literal SHA-256 of "change-me" used to be a popular hand-crafted
        # "secret" — explicitly reject it.
        "0c2c5d3f5cf3eb1ac826b1e5dd2c47b6c4ee1a4b95a8c0a1e9f6c83a16c39c60",
    }
)


def _is_weak_jwt_secret(secret: str, *, env: str) -> tuple[bool, str | None]:
    """
    Decide whether ``secret`` is unsafe to use for signing JWTs.

    Returns ``(is_weak, reason_or_None)``. ``reason`` is a short
    human-readable explanation suitable for logs and error messages.

    Rules (applied in order):
        * ``""`` → weak ("not configured").
        * Normalized value in ``_KNOWN_WEAK_SECRETS`` → weak ("known weak placeholder").
        * In any non-local env, length < ``_JWT_SECRET_MIN_LEN_PROD`` → weak.
        * Local env applies only the first two rules so developers can
          iterate without typing 32-char strings; production demands both.
    """
    if not secret:
        return True, "not configured"
    normalized = secret.strip().lower()
    if normalized in _KNOWN_WEAK_SECRETS:
        return True, "known weak placeholder"
    if env != "local" and len(secret) < _JWT_SECRET_MIN_LEN_PROD:
        return True, (
            f"too short ({len(secret)} chars; need >= {_JWT_SECRET_MIN_LEN_PROD} "
            "in production)"
        )
    return False, None


def _is_valid_fernet_key(raw: str) -> bool:
    """Return True iff ``raw`` is a syntactically valid Fernet key."""
    if not raw:
        return False
    try:
        Fernet(raw.encode("utf-8"))
        return True
    except (ValueError, TypeError):
        return False


def _jwt_secret_or_die() -> str:
    """
    Resolve the canonical JWT signing secret (B-1 fix, hardened).

    Refuses empty, placeholder, AND weak (too-short / known-weak) secrets
    so that misconfigured deployments cannot silently issue forgeable
    tokens. The strict length rule only applies in non-local environments
    so local development is not blocked.
    """
    env = (settings.ENV or "local").strip().lower()
    secret = settings.jwt_signing_secret
    is_weak, reason = _is_weak_jwt_secret(secret, env=env)
    if is_weak:
        raise RuntimeError(
            "JWT signing secret is unsafe ("
            f"{reason}). Set SECRET_KEY (preferred) or JWT_SECRET_KEY in the "
            "environment to a long, random value (>= "
            f"{_JWT_SECRET_MIN_LEN_PROD} chars) before issuing or verifying tokens."
        )
    return secret


def validate_security_config_at_startup() -> None:
    """
    Startup-time validation hook used by ``app.main.lifespan``.

    Checks (B-1 hardening):
        1. JWT signing secret — refuse empty, placeholder, or weak values.
        2. FERNET_KEY (used to encrypt OAuth refresh tokens at rest) —
           in non-local env, must be a syntactically-valid Fernet key. If
           missing or malformed, OAuth token storage falls back to a key
           derived from the JWT secret, which (a) defeats the purpose of
           having FERNET_KEY in the first place and (b) ties two unrelated
           rotation schedules together. Production must boot with a real
           Fernet key.

    Behavior:
        * ``ENV=local``: every issue logs CRITICAL but the app keeps booting.
        * Any other ``ENV``: raises ``RuntimeError`` so the process refuses
          to start with a forgeable token signing key or weak token
          encryption.
    """
    env = (settings.ENV or "local").strip().lower()
    failures: list[str] = []

    is_weak, reason = _is_weak_jwt_secret(settings.jwt_signing_secret, env=env)
    if is_weak:
        failures.append(
            f"JWT signing secret is unsafe ({reason}). Set SECRET_KEY "
            "(preferred) or JWT_SECRET_KEY to a long random value."
        )

    fernet_raw = (settings.FERNET_KEY or "").strip()
    if not fernet_raw or fernet_raw.lower() in _KNOWN_WEAK_SECRETS:
        failures.append(
            "FERNET_KEY is missing or set to a known placeholder. OAuth "
            "tokens at rest must be encrypted with an explicit Fernet key. "
            "Generate one via: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\""
        )
    elif not _is_valid_fernet_key(fernet_raw):
        failures.append(
            "FERNET_KEY is set but is not a valid Fernet key (must be a "
            "url-safe base64-encoded 32-byte key). Regenerate via: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )

    if not failures:
        return

    if env == "local":
        for msg in failures:
            logger.critical("security.misconfiguration: %s", msg)
        return

    joined = " | ".join(failures)
    raise RuntimeError(f"Security misconfiguration ({env}): {joined}")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, email: str) -> str:
    secret = _jwt_secret_or_die()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token() -> str:
    # Random 64-byte hex token (128 hex chars)
    return secrets.token_hex(64)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_access_token(token: str) -> dict[str, Any]:
    secret = _jwt_secret_or_die()
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.JWT_ALGORITHM])
        if not isinstance(payload, dict):
            raise JWTError("Invalid payload")
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = verify_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(str(user_id))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "verify_access_token",
    "validate_security_config_at_startup",
    "get_current_user",
    "_hash_token",
]

