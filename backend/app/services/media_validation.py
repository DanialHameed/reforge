from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any

import ffmpeg  # type: ignore
import httpx


@dataclass(frozen=True)
class MediaInfo:
    kind: str  # "image" | "video" | "unknown"
    bytes: int | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    format_name: str | None
    codec_name: str | None


class MediaValidationError(ValueError):
    pass


def _ratio(w: int, h: int) -> float:
    return float(w) / float(h) if h else 0.0


async def probe_url(media_url: str) -> MediaInfo:
    """
    Download to temp file and probe via ffprobe.

    Note: this is best-effort and intended for preflight validation (not transcoding).
    """
    with tempfile.TemporaryDirectory(prefix="reforge_probe_") as td:
        path = os.path.join(td, "media.bin")
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            r = await client.get(media_url)
            r.raise_for_status()
            data = r.content
            with open(path, "wb") as f:
                f.write(data)

        size = os.path.getsize(path)
        try:
            meta: dict[str, Any] = ffmpeg.probe(path)  # uses ffprobe binary
        except Exception:
            return MediaInfo(kind="unknown", bytes=size, width=None, height=None, duration_seconds=None, format_name=None, codec_name=None)

        streams = meta.get("streams") or []
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        width = int(v.get("width")) if v and v.get("width") else None
        height = int(v.get("height")) if v and v.get("height") else None
        codec_name = (v.get("codec_name") if v else None) or (a.get("codec_name") if a else None)
        duration = None
        try:
            duration = float((meta.get("format") or {}).get("duration"))
        except Exception:
            duration = None
        fmt = (meta.get("format") or {}).get("format_name")
        kind = "video" if v else "image" if width and height else "unknown"
        return MediaInfo(
            kind=kind,
            bytes=size,
            width=width,
            height=height,
            duration_seconds=duration,
            format_name=str(fmt) if fmt else None,
            codec_name=str(codec_name) if codec_name else None,
        )


def validate_for_platform(platform: str, info: MediaInfo) -> None:
    """
    Conservative compatibility checks. Fail fast with actionable errors.
    """
    p = (platform or "").lower()
    if info.kind == "unknown":
        raise MediaValidationError("Could not determine media type. Ensure the URL points to a valid image/video file.")

    # Common sanity
    if info.bytes is not None and info.bytes <= 0:
        raise MediaValidationError("Media file is empty.")
    if info.width and info.height:
        if info.width < 320 or info.height < 320:
            raise MediaValidationError("Media resolution too low (min 320x320).")

    # Platform-specific conservative rules
    if p in {"instagram"}:
        # Feed/reels are sensitive to aspect; allow a wide band but block extreme.
        if info.width and info.height:
            r = _ratio(info.width, info.height)
            if r < 0.56 or r > 1.91:
                raise MediaValidationError("Instagram aspect ratio unsupported. Use ~9:16 to ~1.91:1.")
        if info.kind == "video" and info.duration_seconds and info.duration_seconds > 15 * 60:
            raise MediaValidationError("Instagram video too long (limit 15 minutes for this pipeline).")

    if p in {"facebook"}:
        if info.kind == "video" and info.duration_seconds and info.duration_seconds > 4 * 60 * 60:
            raise MediaValidationError("Facebook video too long.")

    if p in {"twitter", "x"}:
        if info.kind == "video" and info.duration_seconds and info.duration_seconds > 140:
            raise MediaValidationError("Twitter/X video too long (over 140 seconds).")

    if p in {"linkedin"}:
        if info.kind == "video" and info.duration_seconds and info.duration_seconds > 10 * 60:
            raise MediaValidationError("LinkedIn video too long (over 10 minutes).")

    if p in {"youtube"}:
        if info.kind != "video":
            raise MediaValidationError("YouTube publishing requires a video file.")

        fmt_l = (info.format_name or "").lower()
        codec_l = (info.codec_name or "").lower()
        # ffprobe treats JPEG/PNG/etc. as a "video" stream (e.g. mjpeg); that is still a still image file.
        imageish_demuxer = any(
            s in fmt_l for s in ("image2", "jpeg_pipe", "png_pipe", "webp_pipe", "bmp_pipe", "gif", "ico")
        )
        imageish_codec = codec_l in ("mjpeg", "png", "gif", "webp", "bmp", "tiff")
        dur = info.duration_seconds
        no_real_timeline = dur is None or dur < 0.05
        if imageish_demuxer or (imageish_codec and no_real_timeline):
            raise MediaValidationError(
                "YouTube needs a real video file (e.g. MP4), not a still image. "
                "Images will fail YouTube processing; publish the image to Instagram/Facebook/LinkedIn/X instead."
            )

