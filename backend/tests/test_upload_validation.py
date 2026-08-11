"""P-8 layer 1+2+3: ``app.services.upload_validation`` unit tests.

These cover the pure helpers (no FastAPI / DB). The end-to-end behavior
of ``POST /api/v1/content/upload`` is exercised in
``test_security_headers.py``'s e2e fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.upload_validation import (  # noqa: E402
    UploadValidationError,
    assert_magic_matches,
    assert_mime_allowed,
    family_of,
    max_bytes_for,
    normalize_mime,
    safe_extension_for,
    sanitize_extension,
)


# ---------------------------------------------------------------------------
# normalize_mime
# ---------------------------------------------------------------------------


class TestNormalizeMime:
    def test_returns_none_for_none(self) -> None:
        assert normalize_mime(None) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert normalize_mime("") is None

    def test_strips_charset_parameter(self) -> None:
        assert normalize_mime("image/png; charset=utf-8") == "image/png"

    def test_lowercases(self) -> None:
        assert normalize_mime("Image/PNG") == "image/png"

    def test_trims_whitespace(self) -> None:
        assert normalize_mime("  image/png  ") == "image/png"

    def test_resolves_jpg_alias(self) -> None:
        assert normalize_mime("image/jpg") == "image/jpeg"

    def test_resolves_pjpeg_alias(self) -> None:
        assert normalize_mime("image/pjpeg") == "image/jpeg"

    def test_resolves_m4v_alias(self) -> None:
        assert normalize_mime("video/x-m4v") == "video/mp4"


# ---------------------------------------------------------------------------
# assert_mime_allowed
# ---------------------------------------------------------------------------


class TestAssertMimeAllowed:
    @pytest.mark.parametrize(
        "mime",
        ["image/jpeg", "image/png", "image/gif", "image/webp",
         "video/mp4", "video/quicktime", "video/webm", "video/x-matroska"],
    )
    def test_known_good_mimes_pass(self, mime: str) -> None:
        assert assert_mime_allowed(mime) == mime

    def test_alias_is_normalized(self) -> None:
        assert assert_mime_allowed("image/jpg") == "image/jpeg"

    def test_missing_mime_raises(self) -> None:
        with pytest.raises(UploadValidationError, match="Missing"):
            assert_mime_allowed(None)

    def test_empty_mime_raises(self) -> None:
        with pytest.raises(UploadValidationError, match="Missing"):
            assert_mime_allowed("")

    def test_disallowed_mime_lists_allowed(self) -> None:
        with pytest.raises(UploadValidationError) as excinfo:
            assert_mime_allowed("application/x-msdownload")
        assert "Unsupported" in str(excinfo.value)
        # The error must show the allow-list so operators can debug
        # rejections without diving into source.
        assert "image/png" in str(excinfo.value)
        assert "video/mp4" in str(excinfo.value)

    def test_text_html_rejected(self) -> None:
        # Cross-site upload that pretends to be HTML to trick a viewer.
        with pytest.raises(UploadValidationError):
            assert_mime_allowed("text/html")

    def test_application_javascript_rejected(self) -> None:
        with pytest.raises(UploadValidationError):
            assert_mime_allowed("application/javascript")


# ---------------------------------------------------------------------------
# family_of / max_bytes_for / safe_extension_for
# ---------------------------------------------------------------------------


class TestFamilyAndCeilings:
    def test_image_family(self) -> None:
        assert family_of("image/png") == "image"

    def test_video_family(self) -> None:
        assert family_of("video/mp4") == "video"

    def test_image_max_is_smaller_than_video_max(self) -> None:
        assert max_bytes_for("image/png") < max_bytes_for("video/mp4")

    def test_image_max_is_100mb(self) -> None:
        assert max_bytes_for("image/png") == 100 * 1024 * 1024

    def test_video_max_is_2gb(self) -> None:
        assert max_bytes_for("video/mp4") == 2 * 1024 * 1024 * 1024

    @pytest.mark.parametrize(
        "mime,expected",
        [
            ("image/jpeg", ".jpg"),
            ("image/png", ".png"),
            ("image/gif", ".gif"),
            ("image/webp", ".webp"),
            ("video/mp4", ".mp4"),
            ("video/quicktime", ".mov"),
            ("video/webm", ".webm"),
            ("video/x-matroska", ".mkv"),
        ],
    )
    def test_safe_extensions(self, mime: str, expected: str) -> None:
        assert safe_extension_for(mime) == expected


# ---------------------------------------------------------------------------
# sanitize_extension
# ---------------------------------------------------------------------------


class TestSanitizeExtension:
    def test_known_good_with_leading_dot(self) -> None:
        assert sanitize_extension(".png", fallback=".jpg") == ".png"

    def test_known_good_without_leading_dot(self) -> None:
        assert sanitize_extension("png", fallback=".jpg") == ".png"

    def test_uppercase_normalized(self) -> None:
        assert sanitize_extension(".PNG", fallback=".jpg") == ".png"

    def test_empty_returns_fallback(self) -> None:
        assert sanitize_extension("", fallback=".png") == ".png"

    def test_none_returns_fallback(self) -> None:
        assert sanitize_extension(None, fallback=".png") == ".png"

    def test_path_traversal_rejected(self) -> None:
        # ".." would let an attacker write outside the uploads dir.
        assert sanitize_extension(".../etc/passwd", fallback=".png") == ".png"

    def test_overlong_extension_rejected(self) -> None:
        # Beyond 8 chars we treat it as suspicious noise.
        assert sanitize_extension(".verylongextension", fallback=".png") == ".png"

    def test_special_chars_rejected(self) -> None:
        assert sanitize_extension(".php\x00.png", fallback=".jpg") == ".jpg"

    def test_double_dot_rejected(self) -> None:
        assert sanitize_extension("..", fallback=".png") == ".png"

    def test_alphanumeric_short_extension_kept(self) -> None:
        # ``.mp4``, ``.webp`` etc. We don't restrict to the MIME map at
        # this layer — that is the caller's job. We only ensure the
        # string is filesystem-safe.
        assert sanitize_extension(".bin", fallback=".png") == ".bin"


# ---------------------------------------------------------------------------
# assert_magic_matches
# ---------------------------------------------------------------------------


class TestAssertMagicMatches:
    """The dangerous part: file body must back up the claimed Content-Type."""

    def test_real_png_passes(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        assert_magic_matches(png, "image/png")  # does not raise

    def test_real_jpeg_passes(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
        assert_magic_matches(jpeg, "image/jpeg")

    def test_real_gif87_passes(self) -> None:
        assert_magic_matches(b"GIF87a" + b"\x00" * 10, "image/gif")

    def test_real_gif89_passes(self) -> None:
        assert_magic_matches(b"GIF89a" + b"\x00" * 10, "image/gif")

    def test_real_webp_passes(self) -> None:
        # RIFF + 4-byte size + WEBP at offset 8.
        webp = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4
        assert_magic_matches(webp, "image/webp")

    def test_real_mp4_passes(self) -> None:
        mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4
        assert_magic_matches(mp4, "video/mp4")

    def test_real_webm_passes(self) -> None:
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 12
        assert_magic_matches(webm, "video/webm")

    def test_text_pretending_to_be_png_rejected(self) -> None:
        with pytest.raises(UploadValidationError, match="magic bytes"):
            assert_magic_matches(b"<html><body>hi</body>", "image/png")

    def test_pe_executable_pretending_to_be_jpeg_rejected(self) -> None:
        # Windows PE starts with ``MZ``.
        with pytest.raises(UploadValidationError):
            assert_magic_matches(b"MZ\x90\x00\x03\x00\x00\x00", "image/jpeg")

    def test_php_pretending_to_be_png_rejected(self) -> None:
        with pytest.raises(UploadValidationError):
            assert_magic_matches(b"<?php system($_GET[c]); ?>", "image/png")

    def test_partial_signature_rejected(self) -> None:
        # 0xFFD8 is the first 2 bytes of JPEG, but the full magic is
        # 0xFFD8FF. We require the FULL 3-byte prefix.
        with pytest.raises(UploadValidationError):
            assert_magic_matches(b"\xff\xd8\x00" + b"\x00" * 6, "image/jpeg")

    def test_empty_buffer_rejected(self) -> None:
        with pytest.raises(UploadValidationError):
            assert_magic_matches(b"", "image/png")

    def test_unknown_mime_raises(self) -> None:
        # Caller forgot to assert_mime_allowed first.
        with pytest.raises(UploadValidationError, match="Unknown MIME"):
            assert_magic_matches(b"\x89PNG\r\n\x1a\n", "application/x-msdownload")

    def test_riff_without_webp_marker_rejected(self) -> None:
        # WAV / AVI also start with RIFF; the WEBP family marker MUST be
        # at offset 8 to count.
        with pytest.raises(UploadValidationError):
            assert_magic_matches(b"RIFF\x00\x00\x00\x00WAVE", "image/webp")

    def test_quicktime_with_ftyp_passes(self) -> None:
        mov = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 4
        assert_magic_matches(mov, "video/quicktime")
