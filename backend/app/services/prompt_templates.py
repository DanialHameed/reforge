from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel

from app.services.ai_types import MediaAnalysis


class ValidationResult(BaseModel):
    is_valid: bool
    violations: list[str]


PowerWord = Literal[
    "Ultimate",
    "Complete",
    "How to",
    "Why",
    "What Happens When",
    "The Truth About",
    "You Need to Know",
]


POWER_WORDS: tuple[PowerWord, ...] = (
    "Ultimate",
    "Complete",
    "How to",
    "Why",
    "What Happens When",
    "The Truth About",
    "You Need to Know",
)


# ~20+ banned words/phrases (case-insensitive match)
BANNED_WORDS: tuple[str, ...] = (
    "spam",
    "click here",
    "free money",
    "buy now",
    "act now",
    "limited time",
    "guaranteed",
    "get rich",
    "work from home",
    "risk-free",
    "no credit check",
    "earn $",
    "make $",
    "adult",
    "porn",
    "xxx",
    "nude",
    "sex",
    "fetish",
    "kill",
    "suicide",
    "hate",
    "racist",
)


_BANNED_RE = re.compile("|".join(re.escape(w) for w in BANNED_WORDS), re.IGNORECASE)

# Reasonable emoji matcher without external dependency.
# (Covers most emoji blocks; good enough for validation rules here.)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols, etc.
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\u2600-\u26FF"  # Misc symbols
    "\u2700-\u27BF"  # Dingbats
    "]+"
)

_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]+")


def _count_emojis(text: str) -> int:
    if not text:
        return 0
    # Count emoji grapheme runs (approx).
    return len(_EMOJI_RE.findall(text))


def _count_hashtags(text: str) -> int:
    if not text:
        return 0
    return len(_HASHTAG_RE.findall(text))


def _contains_banned(text: str) -> list[str]:
    if not text:
        return []
    matches = {m.group(0).lower() for m in _BANNED_RE.finditer(text)}
    return sorted(matches)


def _iter_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _iter_strings(v)
        return


def _primary_keyword(analysis: MediaAnalysis, prefs: dict) -> str:
    # Allow explicit override; else fall back to analysis main_topic.
    kw = (prefs or {}).get("primary_keyword") or analysis.main_topic
    kw = str(kw).strip()
    return kw if kw else analysis.main_topic


def _pick_power_word(prefs: dict) -> PowerWord:
    pw = (prefs or {}).get("power_word")
    if pw in POWER_WORDS:
        return pw  # type: ignore[return-value]
    return "How to"


class PromptTemplateEngine:
    """
    Generates platform-optimized prompt templates for Gemini.

    This module does NOT call Gemini. It only formats prompts + validates outputs.
    """

    def youtube_title_prompt(self, analysis: MediaAnalysis, prefs: dict) -> str:
        kw = _primary_keyword(analysis, prefs)
        power_word = _pick_power_word(prefs)

        # We explicitly enforce keyword placement + length in the instruction.
        return (
            "You are an expert YouTube SEO title writer.\n"
            "Write ONE YouTube title.\n"
            "Rules:\n"
            f"- Primary keyword MUST appear in the first 3 words: {kw!r}\n"
            "- Length: 50–70 characters\n"
            f"- Include EXACTLY ONE power word/phrase from this list: {list(POWER_WORDS)}\n"
            f"- Use this power word/phrase (exactly once): {power_word!r}\n"
            "- Avoid clickbait or exaggeration\n"
            "- Output ONLY the title text (no quotes, no JSON)\n"
            "Context (analysis):\n"
            f"{analysis.model_dump_json()}\n"
            "Preferences:\n"
            f"{prefs}\n"
        )

    def instagram_caption_prompt(self, analysis: MediaAnalysis, prefs: dict) -> str:
        tone = (prefs or {}).get("tone", "casual")
        return (
            "You are an expert Instagram caption writer.\n"
            "Write ONE caption.\n"
            "Rules:\n"
            f"- Tone: {tone}\n"
            "- Emojis allowed (optional)\n"
            "- Caption length: 150–200 characters\n"
            "- End with a question\n"
            "- Include a natural call-to-action\n"
            "- Do NOT include hashtags in the caption\n"
            "- Output ONLY the caption text (no quotes, no JSON)\n"
            "Context (analysis):\n"
            f"{analysis.model_dump_json()}\n"
            "Preferences:\n"
            f"{prefs}\n"
        )

    def twitter_hook_prompt(self, analysis: MediaAnalysis, prefs: dict) -> str:
        return (
            "You are an expert Twitter/X hook writer.\n"
            "Write the FIRST tweet of a thread.\n"
            "Rules:\n"
            "- Must start with exactly: Thread:\n"
            "- Create curiosity or FOMO\n"
            "- Max 240 characters (leave room for numbering)\n"
            "- Output ONLY the tweet text (no quotes, no JSON)\n"
            "Context (analysis):\n"
            f"{analysis.model_dump_json()}\n"
            "Preferences:\n"
            f"{prefs}\n"
        )

    def linkedin_authority_prompt(self, analysis: MediaAnalysis, prefs: dict) -> str:
        return (
            "You are an expert LinkedIn authority writer.\n"
            "Write ONE LinkedIn post.\n"
            "Rules:\n"
            "- Start with a bold counterintuitive insight or data point\n"
            "- Paragraphs: max 3 sentences each\n"
            "- Include a clear lesson/takeaway\n"
            "- Professional tone\n"
            "- End with a discussion question\n"
            "- Output ONLY the post text (no quotes, no JSON)\n"
            "Context (analysis):\n"
            f"{analysis.model_dump_json()}\n"
            "Preferences:\n"
            f"{prefs}\n"
        )

    def facebook_friend_prompt(self, analysis: MediaAnalysis, prefs: dict) -> str:
        return (
            "You are an expert Facebook writer.\n"
            "Write ONE Facebook post.\n"
            "Rules:\n"
            "- Conversational tone (like talking to a friend)\n"
            "- Natural storytelling\n"
            "- 100–200 characters\n"
            "- Include up to 3 hashtags (optional)\n"
            "- Output ONLY the post text\n"
            "Context (analysis):\n"
            f"{analysis.model_dump_json()}\n"
            "Preferences:\n"
            f"{prefs}\n"
        )


@dataclass(frozen=True)
class _Rule:
    name: str
    check: Callable[[dict[str, Any]], str | None]


def _global_banned_words_rule(content: dict[str, Any]) -> str | None:
    hits: set[str] = set()
    for s in _iter_strings(content):
        hits.update(_contains_banned(s))
    if hits:
        return f"Contains banned words/phrases: {sorted(hits)}"
    return None


def _max_len_rule(field: str, max_len: int) -> Callable[[dict[str, Any]], str | None]:
    def _check(content: dict[str, Any]) -> str | None:
        v = content.get(field)
        if isinstance(v, str) and len(v) > max_len:
            return f"{field} exceeds {max_len} characters (got {len(v)})"
        return None

    return _check


def _emoji_count_rule(field: str, expected: int) -> Callable[[dict[str, Any]], str | None]:
    def _check(content: dict[str, Any]) -> str | None:
        v = content.get(field)
        if isinstance(v, str):
            n = _count_emojis(v)
            if n != expected:
                return f"{field} must contain exactly {expected} emojis (got {n})"
        return None

    return _check


def _hashtag_max_rule(field: str, max_hashtags: int) -> Callable[[dict[str, Any]], str | None]:
    def _check(content: dict[str, Any]) -> str | None:
        v = content.get(field)
        if isinstance(v, str):
            n = _count_hashtags(v)
            if n > max_hashtags:
                return f"{field} must contain ≤ {max_hashtags} hashtags (got {n})"
        return None

    return _check


_PLATFORM_RULES: dict[str, list[_Rule]] = {
    "youtube": [
        _Rule("youtube_title_len", _max_len_rule("title", 100)),
    ],
    "instagram": [
        _Rule("instagram_caption_len", _max_len_rule("caption", 200)),
    ],
    "twitter": [
        _Rule("twitter_tweet_len", _max_len_rule("tweet", 280)),
    ],
    "facebook": [
        _Rule("facebook_hashtags_max", _hashtag_max_rule("post", 3)),
    ],
}


def validate_output(platform: str, content: dict) -> ValidationResult:
    platform = (platform or "").strip().lower()
    violations: list[str] = []

    # Global rules first
    global_msg = _global_banned_words_rule(content)
    if global_msg:
        violations.append(global_msg)

    # Platform-specific rules
    rules = _PLATFORM_RULES.get(platform, [])
    for r in rules:
        msg = r.check(content)
        if msg:
            violations.append(msg)

    return ValidationResult(is_valid=(len(violations) == 0), violations=violations)

