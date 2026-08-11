"""
Real-world bug: `upload.twitter.com/1.1/media/upload.json` failures (INIT / APPEND /
FINALIZE / STATUS) previously bubbled up through bare `httpx.Response.raise_for_status()`,
which only carries the status line ("Client error '403 Forbidden' for url ...") with
no access to X's actual JSON error body. That left `platform_variants.error_message`
completely undiagnosable — a stale OAuth token missing the `media.write` scope and an
X Developer App access tier that doesn't include media uploads both produced the exact
same opaque string, and support had no way to tell them apart from an activity log.

`_raise_for_twitter_upload_stage` fixes this: it parses the response body for the real
`errors[].message` / `detail` / `title` fields, and for 401/403 specifically appends the
two known causes with an actionable next step (reconnect vs. check API tier).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.publishers.twitter_publisher import (  # noqa: E402
    _raise_for_twitter_upload_stage,
)


def _response(status_code: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    request = httpx.Request("POST", "https://upload.twitter.com/1.1/media/upload.json")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, text=text, request=request)


class TestSuccessIsANoop:
    def test_200_does_not_raise(self) -> None:
        _raise_for_twitter_upload_stage(_response(200, {"media_id_string": "1"}), "INIT")

    def test_201_does_not_raise(self) -> None:
        _raise_for_twitter_upload_stage(_response(201, {}), "APPEND")


class TestForbiddenSurfacesActionableGuidance:
    def test_403_includes_stage_and_status_code(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(403, {"errors": []}), "INIT")
        msg = str(exc_info.value)
        assert "INIT" in msg
        assert "403" in msg

    def test_403_surfaces_real_error_body_not_just_status_line(self) -> None:
        # This is the actual regression: the old `raise_for_status()` path could only
        # ever say "403 Forbidden for url ...". The real X error body (e.g. "Your
        # credentials do not allow access to this resource") must now show up.
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(
                _response(403, {"errors": [{"message": "Your credentials do not allow access to this resource."}]}),
                "INIT",
            )
        assert "Your credentials do not allow access to this resource." in str(exc_info.value)

    def test_403_mentions_both_known_causes(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(403, {}), "FINALIZE")
        msg = str(exc_info.value).lower()
        assert "media.write" in msg
        assert "reconnect" in msg
        assert "access tier" in msg or "developer.x.com" in msg

    def test_401_gets_same_actionable_treatment_as_403(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(401, {}), "APPEND")
        msg = str(exc_info.value).lower()
        assert "reconnect" in msg


class TestOtherErrorsStillRaiseWithDetail:
    def test_500_raises_with_stage_and_body_fallback(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(500, text="internal server error"), "STATUS")
        msg = str(exc_info.value)
        assert "STATUS" in msg
        assert "500" in msg

    def test_non_json_body_falls_back_to_raw_text(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(400, text="plain text error"), "APPEND")
        assert "plain text error" in str(exc_info.value)


class TestDetailAndTitleFallbacks:
    def test_detail_field_used_when_no_errors_list(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(403, {"detail": "Forbidden per app settings"}), "INIT")
        assert "Forbidden per app settings" in str(exc_info.value)

    def test_title_field_used_when_no_errors_or_detail(self) -> None:
        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_twitter_upload_stage(_response(403, {"title": "Unauthorized"}), "INIT")
        assert "Unauthorized" in str(exc_info.value)
