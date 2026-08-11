from __future__ import annotations

import logging
import warnings

# google.api_core EOL chatter on Python versions we already run in dev.
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message=r".*Google will stop supporting.*Python version.*google\.api_core.*",
)
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.connections import _frontend_return_url, router as connections_router
from app.api.v1.content import router as content_router
from app.api.v1.migrations import router as migrations_router
from app.api.v1.platforms import router as platforms_router
from app.api.health import router as health_router
from app.core.ai_providers import validate_ai_provider_config_at_startup
from app.core.config import settings
from app.core.database import Base, apply_platform_variant_schema_patches, engine
from app.core.logging import configure_logging
from app.core.runtime_validation import validate_production_runtime_at_startup
from app.core.security import validate_security_config_at_startup

# Ensure model metadata is registered (create_all / Alembic autogenerate)
from app.models import auth_models as _auth_models  # noqa: F401
from app.models import content_orm as _content_orm  # noqa: F401
from app.models import activity_orm as _activity_orm  # noqa: F401
from app.models import social_orm as _social_orm  # noqa: F401
from app.models import connection as _connection  # noqa: F401

logger = logging.getLogger(__name__)


def _is_youtube_oauth_callback(request: Request) -> bool:
    return request.url.path.rstrip("/").endswith("/connections/youtube/callback")


def _is_linkedin_oauth_callback(request: Request) -> bool:
    return request.url.path.rstrip("/").endswith("/connections/linkedin/callback")


def _is_browser_oauth_callback(request: Request) -> bool:
    return _is_youtube_oauth_callback(request) or _is_linkedin_oauth_callback(request)


# Cannot combine allow_credentials=True with wildcard origins; merge env ALLOWED_ORIGINS explicitly.
_CORS_DEV_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
)


def _cors_allow_origins() -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for origin in (*_CORS_DEV_ORIGINS, *settings.allowed_origins_list):
        if origin == "*":
            continue
        if origin not in seen:
            seen.add(origin)
            result.append(origin)
    return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()

    # Refuse to boot in non-local environments without a real JWT signing
    # secret. Local dev only logs CRITICAL so iteration is not blocked.
    validate_security_config_at_startup()

    # B-9: surface AI provider configuration at boot. Local dev only logs
    # the issue (CRITICAL when neither Gemini nor OpenRouter is configured,
    # WARNING for partial setups). Non-local environments raise
    # ``RuntimeError`` if no provider is configured so we never silently
    # serve traffic that produces 100 % static fallback content.
    validate_ai_provider_config_at_startup()

    # P-5: refuse to boot a non-local environment that has eager Celery,
    # an in-memory broker, or SQLite — every one of which silently breaks
    # production but works in dev.
    validate_production_runtime_at_startup()

    # B-7: schema bootstrap is local-only.
    #
    # In local dev we keep ``create_all`` + the legacy ``platform_variants``
    # patcher so newcomers can run the API without first having to apply
    # Alembic migrations. In every other environment the schema is owned
    # exclusively by ``alembic upgrade head`` (run by the deployment script);
    # ``create_all`` is dangerous in production because it will silently
    # mask schema drift between the ORM and the migration history (the exact
    # bug B-7 fixes for ``platform_connections`` and the missing
    # ``social_accounts`` timestamp columns).
    env = (settings.ENV or "local").strip().lower()
    if env == "local":
        async with engine.begin() as conn:
            await conn.run_sync(apply_platform_variant_schema_patches)
            await conn.run_sync(Base.metadata.create_all)
    else:
        logger.info(
            "schema.bootstrap.skipped",
            extra={"env": env, "reason": "production relies on alembic upgrade head"},
        )

    yield


app = FastAPI(lifespan=lifespan, title=settings.APP_NAME, version=settings.API_VERSION)


@app.exception_handler(Exception)
async def _global_exception_fallback(request: Request, exc: Exception):
    """
    OAuth browser redirects cannot show a bare 500 HTML/plain page — bounce to the SPA instead.
    `connections.youtube_callback` already guards most failures; this catches anything that escapes
    (e.g. validation/middleware quirks) without changing behavior for other routes.
    """
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    if isinstance(exc, RequestValidationError):
        if _is_browser_oauth_callback(request):
            return RedirectResponse(
                url=f"{_frontend_return_url()}?status=error&reason=invalid_request",
                status_code=302,
            )
        return await request_validation_exception_handler(request, exc)
    if _is_browser_oauth_callback(request):
        logger.exception("oauth.callback.uncaught_exception", extra={"path": request.url.path})
        return RedirectResponse(
            url=f"{_frontend_return_url()}?status=error&reason=persist_failed",
            status_code=302,
        )
    return PlainTextResponse("Internal Server Error", status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _should_log_http_request(request: Request) -> bool:
    p = request.url.path
    return (
        request.method == "OPTIONS"
        or p.startswith("/api")
        or p == "/health"
        or p.startswith("/health/")
        or p.startswith("/docs")
        or p == "/openapi.json"
    )


@app.middleware("http")
async def _log_http_request(request: Request, call_next):
    """
    Uvicorn access lines can be easy to miss when only watching the app process. This logger
    confirms the ASGI server actually received the request (helps debug wrong API URL / binding).
    """
    response = await call_next(request)
    if _should_log_http_request(request):
        logging.getLogger("reforge.http").info(
            "%s %s -> %s", request.method, request.url.path, response.status_code
        )
    return response


# ---------------------------------------------------------------------------
# P-8: security headers (defense-in-depth alongside the Nginx edge headers
# added in P-4).
#
# The API serves JSON for almost every route; the only HTML it serves is
# Swagger / Redoc on /docs and /redoc. We therefore use TWO CSPs:
#
#   * "lockdown" CSP for JSON/API routes:
#         default-src 'none'; frame-ancestors 'none'; base-uri 'none'
#     A browser cannot load anything from the response, period. This
#     mitigates accidental XSS if an endpoint ever returns text/html.
#
#   * "docs-permissive" CSP for /docs, /redoc, /openapi.json (which
#     Swagger UI loads via a CDN). We allow ``https:`` for scripts and
#     styles, plus ``'unsafe-inline'`` because Swagger emits inline
#     handlers; this matches what FastAPI's bundled docs page needs.
#
# HSTS is only emitted when the request reached us over HTTPS (per RFC
# 6797 — emitting HSTS over plain HTTP is a no-op and confuses dev).
# ---------------------------------------------------------------------------

_CSP_LOCKDOWN = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

_CSP_DOCS = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https:; "
    "style-src 'self' 'unsafe-inline' https:; "
    "img-src 'self' data: https:; "
    "font-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'"
)

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _is_docs_request(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _DOCS_PATHS)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)

    # Headers that always apply.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    )

    # X-Frame-Options is superseded by CSP frame-ancestors but kept for
    # legacy browsers (IE11, very old Safari).
    response.headers.setdefault("X-Frame-Options", "DENY")

    # Per-route CSP.
    csp = _CSP_DOCS if _is_docs_request(request.url.path) else _CSP_LOCKDOWN
    response.headers.setdefault("Content-Security-Policy", csp)

    # HSTS only over HTTPS, and only outside local dev (nobody runs HTTPS
    # on http://localhost:8000 and an accidental HSTS pin there is hard
    # to roll back from a browser).
    if request.url.scheme == "https" and (settings.ENV or "local").lower() != "local":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    return response


app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(content_router, prefix="/api/v1", tags=["content"])
app.include_router(platforms_router, prefix="/api/v1/platforms", tags=["platforms"])
app.include_router(analytics_router, prefix="/api/v1", tags=["analytics"])
app.include_router(migrations_router, prefix="/api/v1", tags=["migrations"])
app.include_router(connections_router, prefix="/api/v1", tags=["connections"])
app.include_router(health_router, prefix="/health", tags=["health"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.API_VERSION}


@app.get("/api/v1/health")
async def health_v1():
    # Frontend expects { name, env, version }
    return {
        "name": settings.APP_NAME,
        "env": settings.ENV,
        "version": settings.API_VERSION,
        "evaluation_mode": settings.EVALUATION_MODE,
    }
