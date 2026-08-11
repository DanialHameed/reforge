"""P-5 regression tests: production runtime validator + Celery eager guard.

The validator must:

1. Refuse to boot in non-local environments when Celery eager mode is on.
2. Refuse to boot in non-local environments when the broker / backend is
   ``memory://`` (in-process queue invisible to real Celery workers).
3. Refuse to boot in non-local environments when DATABASE_URL is SQLite.
4. Stay silent (or info-level only) in ENV=local so dev iteration is not
   blocked.
5. Always log an info-level snapshot of broker / DB / Cloudinary state.

The Celery factory must furthermore force eager mode OFF in production
even if the operator exported the flag — defense in depth for ``celery
worker`` processes that boot without going through ``app.main.lifespan``.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.runtime_validation import (  # noqa: E402
    _is_truthy_env_flag,
    validate_production_runtime_at_startup,
)


_REAL_PG = "postgresql+asyncpg://reforge:pw@db:5432/reforge"
_REAL_BROKER = "redis://redis:6379/0"
_REAL_BACKEND = "redis://redis:6379/1"


def _baseline_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline: a fully-correct production-like Settings snapshot."""
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    monkeypatch.setattr(settings, "CELERY_ALWAYS_EAGER", False)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", _REAL_BROKER)
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", _REAL_BACKEND)
    monkeypatch.setattr(settings, "DATABASE_URL", _REAL_PG)
    monkeypatch.setattr(settings, "CLOUDINARY_CLOUD_NAME", "name")
    monkeypatch.setattr(settings, "CLOUDINARY_API_KEY", "key")
    monkeypatch.setattr(settings, "CLOUDINARY_API_SECRET", "sec")


# ---------------------------------------------------------------------------
# _is_truthy_env_flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (None, False),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        ("no", False),
        ("off", False),
        ("anything-else", False),
    ],
)
def test_is_truthy_env_flag(value: object, expected: bool) -> None:
    assert _is_truthy_env_flag(value) is expected


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_passes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _baseline_prod(monkeypatch)
    with caplog.at_level(logging.INFO, logger="reforge.runtime"):
        validate_production_runtime_at_startup()
    # Snapshot line is always emitted.
    assert any("runtime.validated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Eager mode
# ---------------------------------------------------------------------------


def test_eager_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    assert "eager" in str(ei.value).lower()


def test_legacy_eager_flag_in_production_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_ALWAYS_EAGER", True)
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    assert "celery_always_eager" in str(ei.value).lower()


def test_eager_in_local_only_logs_notice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)
    with caplog.at_level(logging.INFO, logger="reforge.runtime"):
        validate_production_runtime_at_startup()  # must not raise
    assert any("runtime.notice" in r.message and "eager" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Memory broker / backend
# ---------------------------------------------------------------------------


def test_memory_broker_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    assert "memory broker" in str(ei.value).lower() or "celery_broker_url" in str(ei.value).lower()


def test_memory_backend_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "cache+memory://")
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    assert "celery_result_backend" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def test_sqlite_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./reforge.db")
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    assert "sqlite" in str(ei.value).lower()


def test_sqlite_in_local_logs_notice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./reforge.db")
    with caplog.at_level(logging.INFO, logger="reforge.runtime"):
        validate_production_runtime_at_startup()
    assert any("sqlite" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Multiple failures combined
# ---------------------------------------------------------------------------


def test_multiple_failures_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "memory://")
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./reforge.db")
    with pytest.raises(RuntimeError) as ei:
        validate_production_runtime_at_startup()
    msg = str(ei.value).lower()
    assert "eager" in msg
    assert "memory" in msg
    assert "sqlite" in msg


# ---------------------------------------------------------------------------
# Cloudinary missing is a notice not a failure (matches the validator contract).
# ---------------------------------------------------------------------------


def test_missing_cloudinary_is_notice_not_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CLOUDINARY_CLOUD_NAME", None)
    monkeypatch.setattr(settings, "CLOUDINARY_API_KEY", None)
    monkeypatch.setattr(settings, "CLOUDINARY_API_SECRET", None)
    with caplog.at_level(logging.INFO, logger="reforge.runtime"):
        validate_production_runtime_at_startup()  # must not raise
    assert any("cloudinary" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Celery factory: eager forced OFF in non-local even if flag is set.
# ---------------------------------------------------------------------------


def test_celery_factory_forces_eager_off_in_production(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _baseline_prod(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", True)

    # Re-import the celery_app module so the factory runs against the
    # mutated Settings.
    sys.modules.pop("app.workers.celery_app", None)

    with caplog.at_level(logging.ERROR, logger="reforge.celery"):
        celery_module = importlib.import_module("app.workers.celery_app")

    assert celery_module.celery_app.conf.task_always_eager is False
    assert any(
        "celery.eager_refused" in rec.message and "production" in rec.message
        for rec in caplog.records
    )

    # Restore the canonical local-ENV import for the rest of the suite so
    # subsequent tests (e.g. the Gemini pipeline tests) keep their eager
    # local behavior.
    sys.modules.pop("app.workers.celery_app", None)
    monkeypatch.setattr(settings, "ENV", "local")
    importlib.import_module("app.workers.celery_app")
