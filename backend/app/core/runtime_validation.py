"""Production runtime configuration validator (P-5 hardening).

Boot-time guard rails that complement the JWT/AI-provider validators:

* Forbid Celery eager mode in any non-local environment. Eager mode runs
  every dispatched task synchronously inside the caller's thread; in
  production this would freeze the API request handler for the duration
  of a 5-minute video analysis. The compose file already passes
  ``CELERY_TASK_ALWAYS_EAGER=false`` but a future operator typo (or a
  missed ``-e`` override on a one-shot ``docker run``) must not silently
  re-enable it.
* Refuse the in-memory Celery broker / backend in production. A
  ``memory://`` broker means tasks live only inside the API process —
  the entire Celery worker pool is invisible to it, so every task is
  effectively dropped on the floor.
* Refuse a SQLite DATABASE_URL in production. SQLite has no concurrent
  writer support; a multi-uvicorn-worker deploy backed by SQLite
  will silently corrupt under load.
* Surface critical Cloudinary configuration issues — uploads silently
  fall back to local disk when Cloudinary is unconfigured, which works
  in dev but is operationally fragile in production (every restart
  loses the local files unless the volume is correctly mounted).

All checks are pure (no I/O) and execute in well under 1 ms. They are
called from ``app.main.lifespan`` after JWT and AI-provider validation,
so a misconfigured production deploy fails fast at boot rather than
silently degrading.
"""

from __future__ import annotations

import logging

from app.core.config import settings


logger = logging.getLogger("reforge.runtime")


# Memory-only broker / backend URLs. These are the local-dev defaults
# baked into ``Settings``; in production they would silently swallow every
# Celery dispatch.
_MEMORY_BROKER_PREFIXES: tuple[str, ...] = ("memory://", "cache+memory://")


def _is_truthy_env_flag(value: object) -> bool:
    """Return True iff the environment-configured value is logically true.

    Pydantic-settings normalizes string values to ``bool`` for fields typed
    as ``bool``, but defensively-coerced consumers must also handle the
    raw string case (e.g. when the value was assigned via ``setattr``).
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "on"}


def validate_production_runtime_at_startup() -> None:
    """Enforce production-only runtime invariants.

    Behavior:
        * ``ENV=local``: every issue is logged at INFO; the app keeps booting.
        * Any other ``ENV``: collected issues are joined into a single
          ``RuntimeError`` so the process refuses to start.
    """
    env = (settings.ENV or "local").strip().lower()
    failures: list[str] = []
    notices: list[str] = []

    # ------------------------------------------------------------------
    # 1. Celery eager mode is forbidden in non-local environments.
    # ------------------------------------------------------------------
    eager_task = _is_truthy_env_flag(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))
    eager_legacy = _is_truthy_env_flag(getattr(settings, "CELERY_ALWAYS_EAGER", False))
    if eager_task or eager_legacy:
        which = []
        if eager_task:
            which.append("CELERY_TASK_ALWAYS_EAGER")
        if eager_legacy:
            which.append("CELERY_ALWAYS_EAGER")
        msg = (
            f"Celery eager mode is enabled via {', '.join(which)} but ENV is "
            f"{env!r}. Eager mode runs every dispatched task synchronously "
            "inside the caller's thread; in production this freezes the API "
            "request handler. Set both flags to 'false'."
        )
        if env == "local":
            notices.append(msg)
        else:
            failures.append(msg)

    # ------------------------------------------------------------------
    # 2. Real Celery broker required in non-local.
    # ------------------------------------------------------------------
    broker = (settings.CELERY_BROKER_URL or "").strip().lower()
    if any(broker.startswith(p) for p in _MEMORY_BROKER_PREFIXES):
        msg = (
            f"CELERY_BROKER_URL is {broker!r} (memory-only) but ENV is "
            f"{env!r}. The memory broker only lives inside the API "
            "process; Celery workers cannot see it, so every dispatched "
            "task is effectively lost. Configure a real broker "
            "(e.g. redis://redis:6379/0)."
        )
        if env == "local":
            notices.append(msg)
        else:
            failures.append(msg)

    backend = (settings.CELERY_RESULT_BACKEND or "").strip().lower()
    if any(backend.startswith(p) for p in _MEMORY_BROKER_PREFIXES):
        msg = (
            f"CELERY_RESULT_BACKEND is {backend!r} (memory-only) but ENV is "
            f"{env!r}. Configure a real result backend "
            "(e.g. redis://redis:6379/1)."
        )
        if env == "local":
            notices.append(msg)
        else:
            failures.append(msg)

    # ------------------------------------------------------------------
    # 3. SQLite is unsafe under concurrent uvicorn workers.
    # ------------------------------------------------------------------
    db_url = (settings.DATABASE_URL or "").strip().lower()
    if "sqlite" in db_url:
        msg = (
            f"DATABASE_URL is {db_url!r} (SQLite) but ENV is {env!r}. "
            "SQLite has no concurrent-writer support; multi-worker deploys "
            "will deadlock or corrupt the database. Use Postgres "
            "(postgresql+asyncpg://...) in production."
        )
        if env == "local":
            notices.append(msg)
        else:
            failures.append(msg)

    # ------------------------------------------------------------------
    # 4. Cloudinary — non-fatal but operator-visible.
    # ------------------------------------------------------------------
    has_cloudinary = bool(
        (settings.CLOUDINARY_CLOUD_NAME or "").strip()
        and (settings.CLOUDINARY_API_KEY or "").strip()
        and (settings.CLOUDINARY_API_SECRET or "").strip()
    )
    if not has_cloudinary:
        notices.append(
            "Cloudinary is not configured. Uploads will fall back to local "
            "disk under /app/uploads; ensure the uploads_data volume is "
            "mounted and backed up if you intend to run without Cloudinary."
        )

    # ------------------------------------------------------------------
    # 5. Surface notices regardless of env so dev devs get diagnostics.
    # ------------------------------------------------------------------
    for note in notices:
        logger.info("runtime.notice env=%s detail=%s", env, note)

    if not failures:
        logger.info(
            "runtime.validated env=%s broker=%s backend_db=%s cloudinary=%s",
            env,
            broker.split("://", 1)[0] or "<unset>",
            db_url.split("://", 1)[0] or "<unset>",
            "configured" if has_cloudinary else "missing",
        )
        return

    if env == "local":
        for msg in failures:
            logger.warning("runtime.misconfiguration: %s", msg)
        return

    raise RuntimeError(
        "Runtime misconfiguration ("
        f"{env}): " + " | ".join(failures)
    )


__all__ = ["validate_production_runtime_at_startup"]
