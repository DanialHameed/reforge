from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """
    Minimal JSON log formatter (no external deps).

    Emits stable keys so logs are machine-parsable in production.
    """

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach exception info if present
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        # Merge in `extra` fields (logging stores them as attributes on the record)
        reserved = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
        }
        for k, v in record.__dict__.items():
            if k in reserved or k.startswith("_"):
                continue
            base[k] = v
        return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    """
    Configure logging once at process start.

    - Local dev: readable text logs
    - Prod: JSON logs when LOG_FORMAT=json
    """
    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    log_format = (os.getenv("LOG_FORMAT") or "text").lower()

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))

    # Reset handlers (avoid duplicate logs on reload)
    root.handlers.clear()
    root.addHandler(handler)

    # In-memory Celery/Kombu still touches connection code; downgrade noisy "localhost" broker warnings.
    try:
        from app.core.config import settings

        broker = str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip().lower()
        if broker.startswith("memory") or broker == ":///":
            logging.getLogger("kombu.connection").setLevel(logging.ERROR)
    except Exception:
        pass

    # HTTP request lines independent of uvicorn.access (see middleware in `app.main`).
    http_access = logging.getLogger("reforge.http")
    http_access.handlers.clear()
    http_access.addHandler(handler)
    http_access.setLevel(getattr(logging, log_level, logging.INFO))
    http_access.propagate = False

