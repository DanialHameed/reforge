from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # When running locally we typically run from `backend/`, but `.env` lives at repo root.
    # Support both locations.
    _repo_root_env = (Path(__file__).resolve().parents[3] / ".env")
    _backend_env = (Path(__file__).resolve().parents[2] / ".env")

    # Ensure non-Settings env vars (e.g. GOOGLE_API_KEY) are available in os.environ.
    # `pydantic-settings` reads env_file for Settings fields only, but other modules
    # (like AIService) use os.getenv directly.
    # utf-8-sig strips a UTF-8 BOM so keys like LINKEDIN_CLIENT_ID are not prefixed with \ufeff
    # (common when .env is edited with Notepad on Windows).
    load_dotenv(dotenv_path=_repo_root_env, override=False, encoding="utf-8-sig")
    load_dotenv(dotenv_path=_backend_env, override=False, encoding="utf-8-sig")

    model_config = SettingsConfigDict(
        env_file=[str(_repo_root_env), str(_backend_env)],
        extra="ignore",
        case_sensitive=False,
    )

    # App
    ENV: str = "local"
    APP_NAME: str = "ReForge API"
    API_VERSION: str = "2.0"

    # CORS
    # Comma-separated origins (or "*" for any). Example: "http://localhost:3000,https://app.example.com"
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # Auth / JWT
    JWT_SECRET_KEY: str = "change-me"
    # Backward-compatible alias for frameworks expecting SECRET_KEY.
    SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Refresh tokens
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Encryption (Fernet for OAuth tokens at rest)
    # Generate via: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FERNET_KEY: str = "change-me"

    # Database (async URL recommended)
    # Examples:
    # - postgresql+asyncpg://user:pass@localhost:5432/reforge
    # - sqlite+aiosqlite:///./reforge.db
    DATABASE_URL: str = "sqlite+aiosqlite:///./reforge.db"

    # Celery / Redis (Upstash rediss://)
    # Local development default (Windows-friendly): in-memory broker/backend + eager execution.
    # When you move to Redis, override these in `.env`.
    CELERY_BROKER_URL: str = "memory://"
    CELERY_RESULT_BACKEND: str = "cache+memory://"
    CELERY_TASK_ALWAYS_EAGER: bool = True
    CELERY_ALWAYS_EAGER: bool = True
    CELERY_EAGER_PROPAGATES: bool = True

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def _celery_broker_coerce(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "memory://"
        return str(v).strip()

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def _celery_backend_coerce(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "cache+memory://"
        return str(v).strip()

    # Optional publishing integration
    PUBLISHER_WEBHOOK_URL: str | None = None

    # Twitter / X OAuth 2.0 (User Context, PKCE)
    TWITTER_CLIENT_ID: str | None = None
    TWITTER_CLIENT_SECRET: str | None = None
    TWITTER_REDIRECT_URI: str | None = None

    # Google / YouTube OAuth2
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None

    # Meta (Facebook + Instagram Graph) OAuth2
    # One Meta app powers both Facebook Pages and Instagram Business publishing.
    META_APP_ID: str | None = None
    META_APP_SECRET: str | None = None
    META_REDIRECT_URI: str | None = None
    META_GRAPH_VERSION: str = "v19.0"

    # LinkedIn OAuth2
    LINKEDIN_CLIENT_ID: str | None = None
    LINKEDIN_CLIENT_SECRET: str | None = None
    LINKEDIN_REDIRECT_URI: str | None = None

    # Where to bounce the browser back to after OAuth callbacks (frontend).
    OAUTH_FRONTEND_RETURN_URL: str = "http://localhost:3000/connections"

    # Gemini (`google-genai` SDK uses API key explicitly)
    GEMINI_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    # Optional Redis for distributed caches (omit for local/dev without Redis).
    REDIS_URL: str | None = None
    REDIS_BACKEND_URL: str | None = None

    # FastAPI / runtime
    DEBUG: bool = False

    # When true: block destructive API actions (content delete, platform disconnect)
    # and return a no-op "dry run" publish payload when nothing would dispatch—
    # for safe evaluator demos without accidental data loss or live posts.
    EVALUATION_MODE: bool = False

    # Cloudinary (either CLOUDINARY_URL or discrete fields)
    CLOUDINARY_CLOUD_NAME: str | None = None
    CLOUDINARY_API_KEY: str | None = None
    CLOUDINARY_API_SECRET: str | None = None

    @property
    def gemini_api_key(self) -> str:
        """
        Canonical key for Gemini. Prefer GEMINI_API_KEY, fall back to GOOGLE_API_KEY for compatibility.

        Returns the raw configured value with whitespace stripped. Note that
        ``app.core.ai_providers.resolve_gemini_key`` performs the additional
        normalization (placeholder rejection, surrounding-quote stripping)
        used by all production callers; this property remains as a
        lightweight backward-compatible accessor for older code paths and
        the regression suite.
        """
        key = (self.GEMINI_API_KEY or self.GOOGLE_API_KEY or "").strip()
        if key:
            return key
        import os

        return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

    @property
    def openrouter_api_key(self) -> str:
        """
        Canonical key for OpenRouter (the secondary AI provider).

        Mirrors ``gemini_api_key``: prefers the value declared on
        ``Settings`` and falls back to a late ``os.getenv`` read so that
        environment variables exported *after* ``Settings`` was first
        constructed (e.g. by tests using ``monkeypatch.setenv``) still take
        effect.
        """
        key = (self.OPENROUTER_API_KEY or "").strip()
        if key:
            return key
        import os

        return (os.getenv("OPENROUTER_API_KEY") or "").strip()

    @property
    def secret_key(self) -> str:
        """
        Canonical secret key. Prefer SECRET_KEY, fall back to JWT_SECRET_KEY.
        """
        v = (self.SECRET_KEY or self.JWT_SECRET_KEY or "").strip()
        return v

    @property
    def jwt_signing_secret(self) -> str:
        """
        Canonical secret used to sign and verify access JWTs (B-1 fix).

        Resolution order:
        1. ``JWT_SECRET_KEY`` if explicitly set to a non-default value
           (preserves backward compatibility with deployments that only
           configured this variable).
        2. ``SECRET_KEY`` as a fallback (this is what the bundled ``.env`` ships
           with — historically `app.core.security` ignored it, which is the
           root cause of B-1).

        Returns an empty string when neither secret is configured. Callers
        MUST treat an empty return as a hard error and refuse to sign or verify
        tokens — see ``app.core.security._jwt_secret_or_die``.
        """
        for candidate in (self.JWT_SECRET_KEY, self.SECRET_KEY):
            v = (candidate or "").strip()
            if v and v.lower() != "change-me":
                return v
        return ""

    @property
    def allowed_origins_list(self) -> list[str]:
        v = (self.ALLOWED_ORIGINS or "").strip()
        if not v:
            return ["http://localhost:3000"]
        if v == "*":
            return ["*"]
        return [s.strip() for s in v.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

