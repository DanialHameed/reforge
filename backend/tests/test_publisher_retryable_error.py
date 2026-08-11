"""B-20: ``RetryablePublishError`` constructor footgun.

Earlier the class was defined as::

    @dataclass(frozen=True)
    class RetryablePublishError(RuntimeError):
        retry_after_seconds: int

The dataclass-generated ``__init__`` overrode ``RuntimeError.__init__`` and
accepted only ``retry_after_seconds`` as a positional or keyword argument.
Every actual call site looked like::

    raise RetryablePublishError("Twitter rate limit", retry_after_seconds=60)

which collided with the dataclass signature and raised
``TypeError: got multiple values for argument 'retry_after_seconds'``.
The TypeError then bubbled into ``_publish_variant``'s generic
``except Exception``, marking the variant permanently ``failed`` instead of
triggering Celery's retry mechanism. Twitter and Facebook rate-limit handling
were both effectively dead code in production.

These tests pin the new contract: the canonical class lives in
``app.services.publishers.errors``, both publishers re-export the *same*
class object (so ``isinstance`` works regardless of import path), and the
constructor accepts positional ``message`` plus required keyword
``retry_after_seconds`` with safe coercion of garbage values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.publishers.errors import RetryablePublishError  # noqa: E402
from app.services.publishers.facebook_publisher import (  # noqa: E402
    RetryablePublishError as FacebookRetryablePublishError,
)
from app.services.publishers.twitter_publisher import (  # noqa: E402
    RetryablePublishError as TwitterRetryablePublishError,
)


class TestRetryablePublishErrorIdentity:
    """The class re-exported from each publisher must BE the canonical class."""

    def test_facebook_reexport_is_canonical(self) -> None:
        assert FacebookRetryablePublishError is RetryablePublishError

    def test_twitter_reexport_is_canonical(self) -> None:
        assert TwitterRetryablePublishError is RetryablePublishError

    def test_facebook_and_twitter_share_same_class(self) -> None:
        assert FacebookRetryablePublishError is TwitterRetryablePublishError

    def test_is_runtime_error_subclass(self) -> None:
        assert issubclass(RetryablePublishError, RuntimeError)


class TestRetryablePublishErrorConstructor:
    """Constructor must accept ``(message, retry_after_seconds=N)`` without TypeError."""

    def test_twitter_call_site_pattern_does_not_raise_type_error(self) -> None:
        exc = TwitterRetryablePublishError("Twitter rate limit", retry_after_seconds=60)
        assert str(exc) == "Twitter rate limit"
        assert exc.retry_after_seconds == 60

    def test_facebook_call_site_pattern_does_not_raise_type_error(self) -> None:
        exc = FacebookRetryablePublishError(
            "Facebook rate limit. Retry later.", retry_after_seconds=3600
        )
        assert str(exc) == "Facebook rate limit. Retry later."
        assert exc.retry_after_seconds == 3600

    def test_message_optional_default_empty(self) -> None:
        exc = RetryablePublishError(retry_after_seconds=10)
        assert str(exc) == ""
        assert exc.retry_after_seconds == 10

    def test_retry_after_seconds_required_keyword(self) -> None:
        with pytest.raises(TypeError):
            RetryablePublishError("missing kwarg")  # type: ignore[call-arg]

    def test_negative_retry_after_clamped_to_zero(self) -> None:
        exc = RetryablePublishError("x", retry_after_seconds=-9)
        assert exc.retry_after_seconds == 0

    def test_float_retry_after_coerced_to_int(self) -> None:
        exc = RetryablePublishError("x", retry_after_seconds=12.7)  # type: ignore[arg-type]
        assert exc.retry_after_seconds == 12

    def test_garbage_retry_after_coerced_to_zero(self) -> None:
        exc = RetryablePublishError("x", retry_after_seconds="abc")  # type: ignore[arg-type]
        assert exc.retry_after_seconds == 0

    def test_none_retry_after_coerced_to_zero(self) -> None:
        exc = RetryablePublishError("x", retry_after_seconds=None)  # type: ignore[arg-type]
        assert exc.retry_after_seconds == 0


class TestPublishTaskRetryDispatch:
    """``publish_task.publish_content_task`` must recognise the canonical class."""

    def test_publish_task_imports_canonical_error(self) -> None:
        from app.workers import publish_task

        assert publish_task.RetryablePublishError is RetryablePublishError

    def test_isinstance_check_works_across_legacy_import_paths(self) -> None:
        # Code raised the exception via the Twitter/Facebook re-exports;
        # publish_task uses the canonical name. They must be the same class.
        twitter_exc = TwitterRetryablePublishError("rate", retry_after_seconds=120)
        facebook_exc = FacebookRetryablePublishError("rate", retry_after_seconds=120)
        assert isinstance(twitter_exc, RetryablePublishError)
        assert isinstance(facebook_exc, RetryablePublishError)
