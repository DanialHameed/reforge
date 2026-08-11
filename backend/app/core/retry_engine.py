"""
Retry engine for Gemini operations.

The maximum retry delay is hard-capped at 5.0 seconds as a strict UX requirement.
This cap must never be exceeded under any circumstance.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class GeminiRetryEngine:
    BASE_DELAY: float = 0.5
    MAX_DELAY: float = 5.0
    MULTIPLIER: float = 2.0
    MAX_ATTEMPTS: int = 3

    @classmethod
    def execute(
        cls,
        fn: Callable[[], T],
        *,
        model_name: str = "unknown",
        operation: str = "gemini_call",
        on_http_429: Callable[[Exception], None] | None = None,
    ) -> T:
        delay = cls.BASE_DELAY
        last_exc: Exception | None = None

        for attempt in range(1, cls.MAX_ATTEMPTS + 1):
            try:
                out = fn()
                if attempt > 1:
                    logger.info(
                        "%s succeeded after retry",
                        operation,
                        extra={
                            "operation": operation,
                            "model_name": model_name,
                            "attempt": attempt,
                            "max_attempts": cls.MAX_ATTEMPTS,
                        },
                    )
                return out
            except Exception as exc:
                last_exc = exc
                if on_http_429 is not None and cls._looks_like_http_429(exc):
                    try:
                        on_http_429(exc)
                    except Exception:
                        logger.debug("on_http_429 callback failed", exc_info=True)
                if cls._is_terminal_error(exc):
                    logger.error(
                        "retry_engine.terminal_error",
                        extra={
                            "operation": operation,
                            "model": model_name,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:500],
                            "classified_terminal": True,
                        },
                    )
                    raise

                if attempt >= cls.MAX_ATTEMPTS:
                    break

                capped = min(delay, cls.MAX_DELAY)
                logger.warning(
                    "retry_engine.retryable_error",
                    extra={
                        "operation": operation,
                        "model": model_name,
                        "attempt": attempt,
                        "max_attempts": cls.MAX_ATTEMPTS,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:300],
                        "retry_in_seconds": delay,
                    },
                )
                time.sleep(capped)
                delay = min(delay * cls.MULTIPLIER, cls.MAX_DELAY)

        assert last_exc is not None
        logger.error(
            "%s exhausted retries",
            operation,
            extra={
                "operation": operation,
                "model_name": model_name,
                "attempts": cls.MAX_ATTEMPTS,
                "exc_type": type(last_exc).__name__,
                "error_message": str(last_exc)[:500],
            },
        )
        raise last_exc

    @staticmethod
    def _looks_like_http_429(exc: Exception) -> bool:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        try:
            if code is not None and int(code) == 429:
                return True
        except (TypeError, ValueError):
            pass
        s = str(exc).lower()
        if "429" in s and ("too many requests" in s or "resource exhausted" in s or "quota" in s):
            return True
        if "429" in s:
            return True
        details = getattr(exc, "details", None)
        if details and "429" in str(details).lower():
            return True
        return False

    @staticmethod
    def _is_terminal_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        exc_type = type(exc).__name__.lower()  # noqa: F841

        # Only these are truly terminal — not worth retrying
        terminal_signals = [
            "api_key_invalid",
            "api key not valid",
            "invalid api key",
            "permission_denied",
            "caller does not have permission",
            "billing account",
            "account is not authorized",
        ]

        # These look terminal but are NOT — they should retry or fallback gracefully
        # "not found" — could be temporary
        # "safety" — content filtered, use fallback but don't mark terminal
        # "resource_exhausted" — quota, retry with next model
        # "quota" — retry with next model
        # "unavailable" — temporary, retry

        is_terminal = any(sig in msg for sig in terminal_signals)

        return is_terminal
