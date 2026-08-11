from __future__ import annotations

from app.core.config import settings
from app.services.publishers.base import Publisher
from app.services.publishers.facebook_publisher import FacebookPublisher
from app.services.publishers.instagram_publisher import InstagramPublisher
from app.services.publishers.linkedin_publisher import LinkedInPublisher
from app.services.publishers.webhook import WebhookPublisher
from app.services.publishers.twitter_publisher import TwitterPublisher
from app.services.publishers.youtube_publisher import YouTubePublisher


def get_publisher_for_platform(platform: str | None) -> Publisher:
    """
    Map platform -> native publisher. Webhook is only used as fallback for unknown platforms.
    Native publishers do not require PUBLISHER_WEBHOOK_URL.
    """
    p = (platform or "").lower()
    if p == "youtube":
        return YouTubePublisher()  # type: ignore[return-value]
    if p == "facebook":
        return FacebookPublisher()  # type: ignore[return-value]
    if p == "twitter":
        return TwitterPublisher()  # type: ignore[return-value]
    if p == "linkedin":
        return LinkedInPublisher()  # type: ignore[return-value]
    if p == "instagram":
        return InstagramPublisher()  # type: ignore[return-value]

    if not settings.PUBLISHER_WEBHOOK_URL:
        raise RuntimeError("Publishing is not configured (set PUBLISHER_WEBHOOK_URL for this platform).")

    return WebhookPublisher(settings.PUBLISHER_WEBHOOK_URL)

