"""B-13: OpenRouter hardcoded ``data:image/jpeg;base64,...`` MIME.

The previous code unconditionally built::

    data_uri = f"data:image/jpeg;base64,{b64_image}"

regardless of whether the bytes were a JPEG, a PNG, a WebP, or — worst case
— an MP4 video. Two real failure modes resulted:

*   PNG/WebP analyses were sent to vision models tagged as JPEG. Most models
    accept the bytes regardless of the data URI MIME, but some (notably the
    ``meta-llama/llama-3.2-11b-vision-instruct:free`` slug) refuse mismatched
    MIMEs and the entire model chain failed-over to ``openrouter_error``.
*   Video assets fed into ``analyze_and_generate`` (e.g. when the upload
    pipeline routed an .mp4 through the OpenRouter fallback) were base64-
    encoded as ``image/jpeg`` and rejected by every vision endpoint with
    HTTP 400/415, then bubbled into the static fallback after burning ~5
    sequential model attempts × 2 JSON-mode toggles = 10 wasted API calls.

The new ``_resolve_openrouter_image_mime`` returns the actual image MIME or
``None`` for non-image media, letting the caller short-circuit straight to
the static fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.openrouter_service import (  # noqa: E402
    _OPENROUTER_SUPPORTED_IMAGE_MIMES,
    _resolve_openrouter_image_mime,
)


class TestSupportedMimeSet:
    def test_supported_set_matches_vision_endpoint_capabilities(self) -> None:
        assert _OPENROUTER_SUPPORTED_IMAGE_MIMES == {
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
        }


class TestContentTypeHeaderTakesPrecedence:
    def test_jpeg(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.jpg", "image/jpeg") == "image/jpeg"

    def test_png(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.png", "image/png") == "image/png"

    def test_webp(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.webp", "image/webp") == "image/webp"

    def test_gif(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.gif", "image/gif") == "image/gif"

    def test_charset_suffix_stripped(self) -> None:
        assert (
            _resolve_openrouter_image_mime("https://x/y.png", "image/png; charset=utf-8")
            == "image/png"
        )


class TestExtensionFallbackForOpaqueContentType:
    """Cloudinary signed URLs strip Content-Type to ``application/octet-stream``."""

    def test_png_extension(self) -> None:
        assert (
            _resolve_openrouter_image_mime(
                "https://res.cloudinary.com/x/abc.png?v=1", "application/octet-stream"
            )
            == "image/png"
        )

    def test_webp_extension_works_on_python_310(self) -> None:
        # ``mimetypes.guess_type`` does not know ``.webp`` on CPython <= 3.10,
        # which is why we maintain an explicit extension map. This test would
        # have failed under the original implementation on the dev runtime.
        assert (
            _resolve_openrouter_image_mime(
                "https://res.cloudinary.com/x/photo.webp", "application/octet-stream"
            )
            == "image/webp"
        )

    def test_uppercase_extension(self) -> None:
        assert (
            _resolve_openrouter_image_mime(
                "https://x/AB.PNG?signed=1", "application/octet-stream"
            )
            == "image/png"
        )

    def test_jpeg_extension_two_letter_form(self) -> None:
        assert (
            _resolve_openrouter_image_mime("https://x/photo.jpg", "application/octet-stream")
            == "image/jpeg"
        )

    def test_jpeg_extension_four_letter_form(self) -> None:
        assert (
            _resolve_openrouter_image_mime("https://x/photo.jpeg", "application/octet-stream")
            == "image/jpeg"
        )


class TestVideoAndUnknownMediaReturnNone:
    def test_video_mp4_header_returns_none(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.mp4", "video/mp4") is None

    def test_video_extension_only_returns_none(self) -> None:
        # Even without a Content-Type header, a .mp4 URL must not be base64'd
        # into a vision endpoint. Returning None tells the caller to bail.
        assert _resolve_openrouter_image_mime("https://x/y.mp4", None) is None

    def test_no_information_returns_none(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/no-extension", None) is None


class TestGenericImageStarHeaderFallback:
    """A header like ``image/heic`` should still produce a usable JPEG MIME."""

    def test_image_heic_falls_back_to_image_jpeg(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.heic", "image/heic") == "image/jpeg"

    def test_image_avif_falls_back_to_image_jpeg(self) -> None:
        assert _resolve_openrouter_image_mime("https://x/y.avif", "image/avif") == "image/jpeg"
