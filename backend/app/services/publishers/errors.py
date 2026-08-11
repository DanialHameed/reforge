"""
Shared publisher exception types.

We deliberately keep this module dependency-free so it can be imported by
publishers, the publish Celery task, and tests without circular-import risk.
"""

from __future__ import annotations


class RetryablePublishError(RuntimeError):
    """
    Raised by platform publishers when the failure is transient and the Celery
    task should re-attempt publishing after a known delay.

    ``backend/app/workers/publish_task.py`` catches this exception and converts
    it into ``self.retry(countdown=retry_after_seconds)``.

    Historical note (B-20):
        Earlier versions defined this as ``@dataclass(frozen=True)`` inheriting
        from ``RuntimeError``. Dataclass-generated ``__init__`` overrides
        ``RuntimeError.__init__`` and accepts only the dataclass fields as
        positional/keyword arguments. Every call site looked like
        ``RetryablePublishError("Twitter rate limit", retry_after_seconds=60)``,
        which raised ``TypeError: got multiple values for argument
        'retry_after_seconds'`` — meaning Twitter and Facebook rate-limit
        handling never produced a Celery retry. Both branches instead bubbled
        a ``TypeError`` into the generic ``except Exception`` handler in
        ``_publish_variant`` and marked the variant permanently ``failed``.

    The current implementation accepts a positional human-readable message
    (matching ``RuntimeError`` ergonomics) plus a required keyword
    ``retry_after_seconds``. The countdown is coerced to a non-negative int
    because Celery rejects negative countdowns.
    """

    __slots__ = ("retry_after_seconds",)

    def __init__(self, message: str = "", *, retry_after_seconds: int) -> None:
        super().__init__(message)
        try:
            secs = int(retry_after_seconds)
        except (TypeError, ValueError):
            secs = 0
        self.retry_after_seconds = max(0, secs)


__all__ = ["RetryablePublishError"]
