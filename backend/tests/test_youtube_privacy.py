"""B-11: YouTube hardcoded ``privacyStatus="public"``.

The YouTube publisher historically forced uploads to ``public`` in TWO places:

1.  The resumable insert body in ``_upload_video_resumable``.
2.  The post-upload ``videos().update`` call in
    ``_sync_ensure_public_and_embeddable``, which was invoked twice from
    ``_sync_finalize_after_upload`` — once before transcoding and once after.

Even if a caller stored ``privacy_status="unlisted"`` on the variant's
``metadata_json``, the uploaded video would briefly appear unlisted and then
get reset to public by the post-upload update. There was no API surface to
publish private/unlisted YouTube videos at all.

These tests pin the new contract:

*   ``_resolve_youtube_privacy`` reads ``privacy_status`` (or the synonym
    ``youtube_privacy_status``) from ``PlatformVariant.metadata_json``.
*   The allow-list is exactly ``{"public", "unlisted", "private"}``.
*   Anything missing, malformed, or outside the allow-list defaults to
    ``"public"`` — preserving backward-compatible behavior for callers that
    never set the field.
*   ``publicStatsViewable`` is automatically downgraded to ``False`` for
    non-public videos (YouTube ignores the field for those visibilities,
    but we keep the request body internally consistent).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.publishers.youtube_publisher import (  # noqa: E402
    _YOUTUBE_ALLOWED_PRIVACY,
    _YOUTUBE_DEFAULT_PRIVACY,
    _resolve_youtube_privacy,
    _sync_ensure_visibility_and_embeddable,
)


class _FakePV:
    def __init__(self, metadata_json: Any) -> None:
        self.metadata_json = metadata_json


class _FakeVideos:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []

    def update(self, *, part: str, body: dict[str, Any]) -> "_FakeVideosRequest":
        self.update_calls.append({"part": part, "body": body})
        return _FakeVideosRequest()


class _FakeVideosRequest:
    def execute(self) -> dict[str, Any]:
        return {"ok": True}


class _FakeYoutubeClient:
    def __init__(self) -> None:
        self._videos = _FakeVideos()

    def videos(self) -> _FakeVideos:
        return self._videos


class TestPrivacyResolver:
    def test_default_is_public(self) -> None:
        assert _resolve_youtube_privacy(_FakePV(None)) == "public"
        assert _resolve_youtube_privacy(_FakePV({})) == "public"
        assert _resolve_youtube_privacy(None) == "public"

    def test_accepts_allow_listed_values(self) -> None:
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "public"})) == "public"
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "unlisted"})) == "unlisted"
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "private"})) == "private"

    def test_synonym_field_is_recognised(self) -> None:
        pv = _FakePV({"youtube_privacy_status": "unlisted"})
        assert _resolve_youtube_privacy(pv) == "unlisted"

    def test_canonical_field_wins_over_synonym(self) -> None:
        pv = _FakePV({"privacy_status": "private", "youtube_privacy_status": "unlisted"})
        assert _resolve_youtube_privacy(pv) == "private"

    def test_case_and_whitespace_normalised(self) -> None:
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "PRIVATE"})) == "private"
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "  Unlisted  "})) == "unlisted"

    def test_unknown_value_defaults_to_public(self) -> None:
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": "draft"})) == "public"
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": ""})) == "public"
        assert _resolve_youtube_privacy(_FakePV({"privacy_status": None})) == "public"

    def test_non_dict_metadata_does_not_crash(self) -> None:
        assert _resolve_youtube_privacy(_FakePV([])) == "public"
        assert _resolve_youtube_privacy(_FakePV("invalid")) == "public"

    def test_allow_list_is_immutable_set(self) -> None:
        assert isinstance(_YOUTUBE_ALLOWED_PRIVACY, frozenset)
        assert _YOUTUBE_ALLOWED_PRIVACY == {"public", "unlisted", "private"}
        assert _YOUTUBE_DEFAULT_PRIVACY == "public"


class TestEnsureVisibilityAndEmbeddable:
    def test_public_request_sets_publicStatsViewable_true(self) -> None:
        yt = _FakeYoutubeClient()
        _sync_ensure_visibility_and_embeddable(yt, "vid_pub", "public")
        body = yt._videos.update_calls[0]["body"]
        assert body["status"]["privacyStatus"] == "public"
        assert body["status"]["publicStatsViewable"] is True
        assert body["status"]["embeddable"] is True
        assert body["id"] == "vid_pub"

    def test_unlisted_request_disables_publicStatsViewable(self) -> None:
        yt = _FakeYoutubeClient()
        _sync_ensure_visibility_and_embeddable(yt, "vid_unl", "unlisted")
        body = yt._videos.update_calls[0]["body"]
        assert body["status"]["privacyStatus"] == "unlisted"
        assert body["status"]["publicStatsViewable"] is False

    def test_private_request_disables_publicStatsViewable(self) -> None:
        yt = _FakeYoutubeClient()
        _sync_ensure_visibility_and_embeddable(yt, "vid_priv", "private")
        body = yt._videos.update_calls[0]["body"]
        assert body["status"]["privacyStatus"] == "private"
        assert body["status"]["publicStatsViewable"] is False

    def test_unknown_privacy_falls_back_to_public(self) -> None:
        yt = _FakeYoutubeClient()
        _sync_ensure_visibility_and_embeddable(yt, "vid_x", "banana")
        body = yt._videos.update_calls[0]["body"]
        assert body["status"]["privacyStatus"] == "public"
        assert body["status"]["publicStatsViewable"] is True

    def test_https_status_update_failure_is_swallowed(self) -> None:
        # The post-upload visibility update is best-effort; an HttpError must
        # not propagate out of the helper or it would crash the publish task
        # after the video already uploaded successfully.
        from googleapiclient.errors import HttpError

        class _FailingVideos:
            def update(self, *, part: str, body: dict[str, Any]) -> "_FailingRequest":
                return _FailingRequest()

        class _FailingRequest:
            def execute(self) -> Any:
                class _Resp:
                    status = 500
                    reason = "Internal"

                raise HttpError(_Resp(), b"boom", uri="https://example.com")

        class _FailingYT:
            def videos(self) -> _FailingVideos:
                return _FailingVideos()

        # Must not raise.
        _sync_ensure_visibility_and_embeddable(_FailingYT(), "vid_fail", "private")
