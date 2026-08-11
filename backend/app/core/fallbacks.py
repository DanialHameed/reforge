"""
Centralized fallback payloads. These are served
when Gemini AI is unavailable. Schema must match PlatformVariant exactly.
"""

from __future__ import annotations

from typing import Any


def get_all_platform_fallbacks() -> dict[str, Any]:
    return {
        "instagram": {
            "caption": "Amazing content worth sharing! 🚀✨",
            "hashtags": ["#content", "#reforge", "#socialmedia"],
            "story_text": "Check this out!",
        },
        "twitter": {
            "tweet": "Just dropped some amazing content! Check it out 🚀 #reforge",
            "thread": [
                "Just dropped some amazing content! 🚀",
                "Head over to see the full thing. Worth it. #reforge",
            ],
        },
        "linkedin": {
            "post": "Excited to share this piece of content with my network. Quality work speaks for itself. What do you think?",
            "hashtags": ["#content", "#professional", "#reforge"],
        },
        "facebook": {
            "post": "Check out this amazing content! Would love to hear your thoughts 💬",
            "hashtags": ["#content", "#community", "#reforge"],
        },
        "youtube": {
            "title": "Amazing Content You Need to See",
            "description": "Incredible content created with Reforge. Like and subscribe for more amazing posts!",
            "tags": ["content", "reforge", "socialmedia", "creator"],
        },
    }


def get_instagram_fallback() -> dict:
    return get_all_platform_fallbacks()["instagram"]


def get_twitter_fallback() -> dict:
    return get_all_platform_fallbacks()["twitter"]


def get_linkedin_fallback() -> dict:
    return get_all_platform_fallbacks()["linkedin"]


def get_facebook_fallback() -> dict:
    return get_all_platform_fallbacks()["facebook"]


def get_youtube_fallback() -> dict:
    return get_all_platform_fallbacks()["youtube"]

