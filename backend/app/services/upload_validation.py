"""
Upload validation for ``POST /api/v1/content/upload``.

Three layers of defense, in order:

    1. **MIME allow-list** — the request's ``Content-Type`` must be one we
       explicitly publish to a downstream platform. Anything outside the
       allow-list is rejected with 415, regardless of body.
    2. **Filename sanitization** — the original filename is *never* used
       on disk. We derive a UUID-based name and constrain the extension
       to the allow-list mapped from the MIME family. This eliminates
       path-traversal vectors (``../etc/passwd.png``) and double-extension
       attacks (``invoice.pdf.exe``).
    3. **Magic-byte sniff** — after the upload is buffered to a temp
       file, we read the first ~16 bytes and verify they match a signature
       belonging to the claimed MIME family. A user lying about
       ``Content-Type: video/mp4`` while sending a ``.exe`` is rejected
       here, even if step 1 passed.

We deliberately avoid ``python-magic`` because:

    * libmagic is a native dep that is rarely installed on Windows dev
      machines (would break local iteration).
    * Our allow-list is small (~10 formats); a hand-rolled signature
      table is clearer, dependency-free, and easy to extend as we add
      platforms.

This module raises :class:`UploadValidationError` for every rejection;
the API layer converts that to an HTTPException so the precise reason
is logged once at the boundary.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, Mapping

logger = logging.getLogger(__name__)


class UploadValidationError(ValueError):
    """Raised when an upload fails any validation step."""


# ---------------------------------------------------------------------------
# Allow-list: MIME -> (canonical extension, magic-byte signatures)
#
# Signatures are tuples of ``(byte_offset, expected_bytes)``. A file
# matches the family if ANY of its tuple lists fully matches the buffer.
#
# Source for video signatures:
#   * mp4 / mov: ISO Base Media File Format, "ftyp" box at offset 4.
#   * webm / mkv: EBML header 0x1A45DFA3 at offset 0.
# Source for image signatures:
#   * jpeg: 0xFFD8FF
#   * png:  0x89 0x50 0x4E 0x47 0x0D 0x0A 0x1A 0x0A
#   * gif:  "GIF87a" or "GIF89a"
#   * webp: "RIFF....WEBP" at offsets 0 and 8
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MimeSpec:
    extension: str
    # Each inner tuple is one ``(offset, expected_bytes)`` constraint.
    # Each outer entry is one valid signature; a buffer matches the spec
    # if ANY signature fully matches.
    signatures: tuple[tuple[tuple[int, bytes], ...], ...]


_ALLOWED: Final[Mapping[str, _MimeSpec]] = {
    # Images
    "image/jpeg": _MimeSpec(
        extension=".jpg",
        signatures=(((0, b"\xff\xd8\xff"),),),
    ),
    "image/png": _MimeSpec(
        extension=".png",
        signatures=(((0, b"\x89PNG\r\n\x1a\n"),),),
    ),
    "image/gif": _MimeSpec(
        extension=".gif",
        signatures=(
            ((0, b"GIF87a"),),
            ((0, b"GIF89a"),),
        ),
    ),
    "image/webp": _MimeSpec(
        extension=".webp",
        signatures=(((0, b"RIFF"), (8, b"WEBP")),),
    ),
    # Videos
    "video/mp4": _MimeSpec(
        extension=".mp4",
        # ftyp + ANY brand at offset 4 — we check "ftyp" at offset 4 and
        # accept the rest. Common brands: isom, mp42, MSNV, avc1, dash.
        signatures=(((4, b"ftyp"),),),
    ),
    "video/quicktime": _MimeSpec(
        extension=".mov",
        signatures=(((4, b"ftyp"),),),
    ),
    "video/webm": _MimeSpec(
        extension=".webm",
        signatures=(((0, b"\x1a\x45\xdf\xa3"),),),
    ),
    "video/x-matroska": _MimeSpec(
        extension=".mkv",
        signatures=(((0, b"\x1a\x45\xdf\xa3"),),),
    ),
}


# Some browsers send legacy aliases. Normalize before lookup.
_MIME_ALIASES: Final[Mapping[str, str]] = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "video/mpeg4": "video/mp4",
    "video/x-m4v": "video/mp4",
    "application/mp4": "video/mp4",
}


_MAX_BYTES: Final[Mapping[str, int]] = {
    # video/* gets the larger ceiling, image/* the smaller.
    "video": 2 * 1024 * 1024 * 1024,  # 2 GB
    "image": 100 * 1024 * 1024,        # 100 MB
}


_SAFE_EXT_RE: Final[re.Pattern[str]] = re.compile(r"^\.[a-z0-9]{1,8}$")


def normalize_mime(raw: str | None) -> str | None:
    """Lowercase and de-alias the client-provided ``Content-Type``.

    Returns ``None`` for empty / unknown input so callers raise an
    explicit 400.
    """
    if not raw:
        return None
    base = raw.split(";", 1)[0].strip().lower()
    if not base:
        return None
    return _MIME_ALIASES.get(base, base)


def family_of(mime: str) -> str:
    """``"image/png"`` -> ``"image"``. Returns the prefix before ``/``."""
    return mime.split("/", 1)[0]


def assert_mime_allowed(mime: str | None) -> str:
    """Raise unless ``mime`` is in the allow-list. Returns the canonical MIME."""
    canonical = normalize_mime(mime)
    if canonical is None:
        raise UploadValidationError("Missing or empty Content-Type")
    if canonical not in _ALLOWED:
        raise UploadValidationError(
            f"Unsupported MIME type: {canonical}. "
            "Allowed: " + ", ".join(sorted(_ALLOWED.keys()))
        )
    return canonical


def max_bytes_for(mime: str) -> int:
    """Per-family upload ceiling. Falls back to the image ceiling."""
    return _MAX_BYTES.get(family_of(mime), _MAX_BYTES["image"])


def safe_extension_for(mime: str) -> str:
    """Canonical, server-controlled extension for a validated MIME."""
    return _ALLOWED[mime].extension


def sanitize_extension(raw: str | None, *, fallback: str) -> str:
    """Reduce a user-supplied filename suffix to a known-safe extension.

    ``raw`` is whatever the client sent (e.g. ``".PNG"``, ``"png"``,
    ``""``, ``None``, ``".../etc/passwd"``). We accept *only* a leading
    dot followed by 1–8 alphanumeric characters. Anything else is
    discarded in favor of ``fallback`` (which the caller derives from
    the validated MIME).
    """
    if not raw:
        return fallback
    candidate = raw if raw.startswith(".") else f".{raw}"
    candidate = candidate.lower()
    if _SAFE_EXT_RE.match(candidate):
        return candidate
    return fallback


def assert_magic_matches(buffer: bytes, mime: str) -> None:
    """Verify the file body's magic bytes match the claimed ``mime``.

    Raises :class:`UploadValidationError` if no signature matches. We
    only sniff the first few bytes (callers should pass at least 16),
    so the cost is constant regardless of file size.
    """
    spec = _ALLOWED.get(mime)
    if spec is None:
        # Should never happen — caller must have run ``assert_mime_allowed``
        # already. Fail loud rather than silent.
        raise UploadValidationError(f"Unknown MIME for magic check: {mime}")

    for signature in spec.signatures:
        if all(
            buffer[offset : offset + len(needle)] == needle
            for offset, needle in signature
        ):
            return

    head = buffer[:8].hex()
    raise UploadValidationError(
        f"File body does not match Content-Type {mime} "
        f"(magic bytes head={head})"
    )


__all__ = [
    "UploadValidationError",
    "assert_magic_matches",
    "assert_mime_allowed",
    "family_of",
    "max_bytes_for",
    "normalize_mime",
    "safe_extension_for",
    "sanitize_extension",
]
