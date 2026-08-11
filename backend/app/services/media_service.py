from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResizedMedia:
    platform: str
    url: str


class MediaService:
    """
    Minimal media utility layer used by the Celery pipeline.

    This is intentionally conservative: it does NOT do heavy transcoding in local-dev,
    and falls back to returning the original URL when no processing is needed.
    """

    def detect_format(self, file_url: str, file_type: str | None) -> str:
        if file_type in {"video", "image"}:
            return file_type
        u = (file_url or "").lower()
        if any(u.endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".webm")):
            return "video"
        if any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return "image"
        return "video"  # safe default for your pipeline

    def resize_for_platform(self, file_url: str, platform: str, file_type: str) -> ResizedMedia:
        """
        Best-effort "URL-only" transformations.

        - If the file is hosted on Cloudinary, inject a crop/aspect transformation
          suitable for the platform. This avoids many publish-time rejections.
        - If not Cloudinary, return the original URL (no heavy local transcoding).
        """
        url = file_url
        p = (platform or "").lower().strip()

        def cloudinary_transform(u: str, *, aspect: str) -> str:
            if "res.cloudinary.com" not in u or "/upload/" not in u:
                return u
            t = f"c_fill,ar_{aspect},g_auto"
            return u.replace("/upload/", f"/upload/{t}/", 1)

        # Instagram: default to feed-safe square unless metadata marks reels elsewhere.
        if p == "instagram":
            url = cloudinary_transform(url, aspect="1:1")
        # Facebook: keep a mild landscape-safe crop for thumbnails; videos are handled by the platform.
        elif p == "facebook":
            url = cloudinary_transform(url, aspect="1.91:1")
        # LinkedIn: square tends to be safest for feed previews.
        elif p == "linkedin":
            url = cloudinary_transform(url, aspect="1:1")
        # Twitter/X: do not crop by default (cropping can hurt intent); keep original.

        return ResizedMedia(platform=platform, url=url)

