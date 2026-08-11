"""B-18: Twitter media-type heuristic.

The original ``upload_media`` decided "is this a video?" with::

    is_video = (
        file_url.lower().split("?")[0].endswith((".mp4", ".mov", ".webm"))
        or size > 5 * 1024 * 1024
    )
    media_type = "video/mp4" if is_video else "image/jpeg"

Two real failure modes resulted:

*   A 6 MB PNG (well below Twitter's 5 MB image limit on PNG, but above the
    branch's 5 MB threshold) was classified as ``video/mp4``. The chunked
    APPEND endpoint then fed PNG bytes into an MP4 INIT, and FINALIZE
    rejected with ``UnknownMediaError``.
*   A non-MP4/MOV/WEBM video container (``.mkv``, ``.avi``, ``.mpeg``, or
    a Cloudinary signed URL with no extension at all) was classified as
    ``image/jpeg`` and INIT/APPEND silently succeeded, but FINALIZE rejected
    the bytes — the user saw a confusing "Twitter media INIT did not return
    media_id_string" error several seconds into the upload.

The new ``_resolve_twitter_media_type`` resolves MIME from the response
``Content-Type`` first, then ``mimetypes.guess_type`` against the URL path,
and only falls back to ``image/jpeg`` when nothing more authoritative is
available. Generic ``image/*`` and ``video/*`` hints get mapped to the
closest Twitter-supported representative so we never end up sending a bare
``image/heic`` or ``video/x-matroska`` to INIT (Twitter ``415``s those).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.publishers.twitter_publisher import (  # noqa: E402
    _TWITTER_SUPPORTED_IMAGE_MIMES,
    _TWITTER_SUPPORTED_VIDEO_MIMES,
    _resolve_twitter_media_type,
)


class TestSupportedMimeSets:
    def test_image_supported_set_matches_twitter_v1_1_docs(self) -> None:
        assert _TWITTER_SUPPORTED_IMAGE_MIMES == {
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        }

    def test_video_supported_set_matches_twitter_v1_1_docs(self) -> None:
        assert _TWITTER_SUPPORTED_VIDEO_MIMES == {
            "video/mp4",
            "video/quicktime",
            "video/webm",
        }


class TestContentTypeHeaderTakesPrecedence:
    def test_png_content_type_returns_image_png(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.png", "image/png") == "image/png"

    def test_jpeg_content_type_returns_image_jpeg(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.jpg", "image/jpeg") == "image/jpeg"

    def test_gif_content_type_returns_image_gif(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.gif", "image/gif") == "image/gif"

    def test_webp_content_type_returns_image_webp(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.webp", "image/webp") == "image/webp"

    def test_mp4_content_type_returns_video_mp4(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.mp4", "video/mp4") == "video/mp4"

    def test_quicktime_content_type_returns_video_quicktime(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.mov", "video/quicktime") == "video/quicktime"

    def test_webm_content_type_returns_video_webm(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.webm", "video/webm") == "video/webm"

    def test_charset_suffix_is_stripped(self) -> None:
        assert (
            _resolve_twitter_media_type("https://x/y.png", "image/png; charset=utf-8")
            == "image/png"
        )


class TestExtensionFallbackWhenContentTypeIsOpaque:
    """Cloudinary signed URLs frequently strip Content-Type to ``application/octet-stream``."""

    def test_octet_stream_with_png_extension(self) -> None:
        assert (
            _resolve_twitter_media_type(
                "https://res.cloudinary.com/x/abc.png?v=1", "application/octet-stream"
            )
            == "image/png"
        )

    def test_octet_stream_with_mp4_extension(self) -> None:
        assert (
            _resolve_twitter_media_type(
                "https://res.cloudinary.com/x/clip.mp4?v=1", "application/octet-stream"
            )
            == "video/mp4"
        )

    def test_no_header_with_webm_extension(self) -> None:
        assert _resolve_twitter_media_type("https://x/clip.webm", None) == "video/webm"


class TestUnsupportedTypesAreMappedToClosestRepresentative:
    """We never want to send Twitter a ``415``-causing MIME like ``image/heic``."""

    def test_image_heic_maps_to_image_jpeg(self) -> None:
        assert _resolve_twitter_media_type("https://x/y.heic", "image/heic") == "image/jpeg"

    def test_video_matroska_maps_to_video_mp4(self) -> None:
        # Twitter doesn't accept MKV; map it down to the lowest-common-denominator video MIME.
        assert (
            _resolve_twitter_media_type("https://x/clip.mkv", "video/x-matroska")
            == "video/mp4"
        )


class TestFinalFallbackOnTotallyAnonymousMedia:
    def test_no_extension_no_header_falls_back_to_image_jpeg(self) -> None:
        # Conservative fallback — most "anonymous" assets are profile images
        # that turn out to be JPEG. A wrong fallback here means INIT fails
        # quickly, which is better than the old behavior of silently corrupting
        # large PNG uploads.
        assert _resolve_twitter_media_type("https://x/no-extension-or-headers", None) == "image/jpeg"


class TestRegressionForOriginalBugs:
    """Encodes the exact failure modes the helper was written to fix."""

    def test_six_megabyte_png_is_no_longer_classified_as_video(self) -> None:
        # The original code did `is_video = ... or size > 5*MB`. With the new
        # helper, size is irrelevant and a properly-typed PNG stays PNG.
        assert (
            _resolve_twitter_media_type(
                "https://res.cloudinary.com/x/big.png", "image/png"
            )
            == "image/png"
        )

    def test_mkv_video_is_no_longer_classified_as_image_jpeg(self) -> None:
        # Old: extension not in (.mp4, .mov, .webm) AND size <= 5MB → image/jpeg.
        # New: video/* Content-Type is honoured and downgraded to video/mp4.
        assert (
            _resolve_twitter_media_type("https://x/clip.mkv", "video/x-matroska")
            == "video/mp4"
        )
