from __future__ import annotations

import base64
import logging
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.auth_models import User
from app.models.connection import PlatformConnection
from app.models.social_orm import SocialAccount
from app.services.token_crypto import encrypt_token


router = APIRouter(prefix="/connections")
logger = logging.getLogger(__name__)

GOOGLE_AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# `youtube.upload` covers `videos().insert`. The broader `youtube` scope is required
# for the post-upload `videos().update` call (re-applying privacy / embeddable) and
# is a superset of `youtube.readonly`, so `videos().list` / `channels().list` keep working.
# Without it the upload succeeds and the status update 403s with insufficientPermissions.
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# Twitter / X OAuth 2.0 with PKCE
TWITTER_AUTH_BASE = "https://twitter.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWITTER_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "media.write",
    "offline.access",
]

# Meta (Facebook + Instagram) OAuth 2.0
def _meta_graph_version() -> str:
    return (settings.META_GRAPH_VERSION or "v19.0").strip()


def _meta_auth_base() -> str:
    return f"https://www.facebook.com/{_meta_graph_version()}/dialog/oauth"


def _meta_token_url() -> str:
    return f"https://graph.facebook.com/{_meta_graph_version()}/oauth/access_token"


# Minimum scope set required by our publishers:
#   pages_show_list           -> /me/accounts to enumerate Pages
#   pages_read_engagement     -> required to receive page access tokens
#   pages_manage_posts        -> POST to /{page-id}/photos and /{page-id}/videos
#   instagram_basic           -> read instagram_business_account on the page
#   instagram_content_publish -> POST to /{ig-user-id}/media + /media_publish
# `pages_manage_engagement` (likes/comments admin) and `business_management`
# (Business Manager admin) are intentionally NOT requested:
#   - We never call those endpoints, so requesting them is dead surface.
#   - `business_management` requires Business Verification + App Review and is
#     the most common reason the OAuth dialog returns "Invalid Scopes" before
#     a Meta App is fully reviewed; dropping it lets app-role testers connect
#     immediately without waiting on Review.
META_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
]

# LinkedIn OAuth 2.0
LINKEDIN_AUTH_BASE = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_PROFILE_URL = "https://api.linkedin.com/v2/userinfo"
# Default: OpenID + posting. If LinkedIn shows "Bummer, something went wrong", your app may
# not have a Product that allows `w_member_social`; set LINKEDIN_OAUTH_SCOPES (see .env.example).
LINKEDIN_SCOPES_DEFAULT = ["openid", "profile", "email", "w_member_social"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _sign_state(payload_b64: str) -> str:
    secret = (settings.secret_key or "").encode("utf-8")
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url(sig)


def _make_state(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "ts": int(time.time()),
        "nonce": secrets.token_hex(16),
    }
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{payload_b64}.{_sign_state(payload_b64)}"


def _parse_state(state: str, max_age_seconds: int = 600) -> str | None:
    if not state or "." not in state:
        return None
    payload_b64, sig = state.split(".", 1)
    expected = _sign_state(payload_b64)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        uid = str(payload.get("uid") or "")
        ts = int(payload.get("ts") or 0)
        if not uid or ts <= 0:
            return None
        if int(time.time()) - ts > max_age_seconds:
            return None
        # validate uuid
        uuid.UUID(uid)
        return uid
    except Exception:
        return None


def _google_client_id() -> str:
    v = (getattr(settings, "GOOGLE_CLIENT_ID", None) or "").strip()
    if not v:
        import os

        v = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    return v


def _google_client_secret() -> str:
    v = (getattr(settings, "GOOGLE_CLIENT_SECRET", None) or "").strip()
    if not v:
        import os

        v = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    return v


def _google_redirect_uri() -> str:
    v = (getattr(settings, "GOOGLE_REDIRECT_URI", None) or "").strip()
    if not v:
        import os

        v = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()
    return v


@router.get("/youtube/authorize")
async def youtube_authorize(
    user: User = Depends(get_current_user),
):
    client_id = _google_client_id()
    redirect_uri = _google_redirect_uri()
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth is not configured (GOOGLE_CLIENT_ID/GOOGLE_REDIRECT_URI).",
        )

    state = _make_state(str(user.id))
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    url = f"{GOOGLE_AUTH_BASE}?{urllib.parse.urlencode(q)}"
    return {"authorize_url": url}


@router.get("/youtube/callback")
async def youtube_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    # We cannot rely on Authorization headers on this redirect.
    try:
        uid = _parse_state(state or "")
        if not uid:
            return RedirectResponse(url=f"{_frontend_return_url()}?status=error&reason=invalid_state", status_code=302)

        if not code:
            return RedirectResponse(url=f"{_frontend_return_url()}?status=error&reason=missing_code", status_code=302)

        client_id = _google_client_id()
        client_secret = _google_client_secret()
        redirect_uri = _google_redirect_uri()
        if not client_id or not client_secret or not redirect_uri:
            return RedirectResponse(url=f"{_frontend_return_url()}?status=error&reason=oauth_not_configured", status_code=302)

        token_req = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(GOOGLE_TOKEN_URL, data=token_req)
                r.raise_for_status()
                tok = r.json()
        except Exception:
            logger.warning(
                "youtube.oauth.token_exchange_failed",
                extra={"hint": "Check GOOGLE_* env, redirect URI in Google Cloud, and that the code was not reused."},
            )
            return RedirectResponse(url=f"{_frontend_return_url()}?status=error&reason=token_exchange_failed", status_code=302)

        access_token = str(tok.get("access_token") or "").strip() or None
        refresh_token = str(tok.get("refresh_token") or "").strip() or None
        expires_in = tok.get("expires_in")
        scope = str(tok.get("scope") or "").strip() or None

        expires_at = None
        try:
            if expires_in is not None:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        except Exception:
            expires_at = None

        user_uuid = uuid.UUID(uid)
        existing = (
            await db.execute(
                select(PlatformConnection).where(
                    PlatformConnection.user_id == user_uuid,
                    PlatformConnection.platform == "youtube",
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            conn = PlatformConnection(
                user_id=user_uuid,
                platform="youtube",
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scopes=scope,
            )
            db.add(conn)
        else:
            existing.access_token = access_token
            if refresh_token:
                existing.refresh_token = refresh_token
            existing.expires_at = expires_at
            existing.scopes = scope
            db.add(existing)

        await db.commit()
        # Also persist into encrypted social_accounts (publishers read this).
        await _upsert_social_account(
            db,
            user_id=user_uuid,
            platform="youtube",
            platform_user_id=None,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            metadata={},
        )
        await db.commit()
        return RedirectResponse(
            url=f"{_frontend_return_url()}?status=success&platform=youtube",
            status_code=302,
        )
    except Exception:
        # Never return a raw 500 to the browser during OAuth redirect.
        logger.exception("youtube.oauth.persist_failed")
        try:
            await db.rollback()
        except Exception:
            pass
        return RedirectResponse(url=f"{_frontend_return_url()}?status=error&reason=persist_failed", status_code=302)


# =============================================================================
# Generic helpers shared by Twitter / Meta / LinkedIn flows
# =============================================================================


def _frontend_return_url() -> str:
    v = (getattr(settings, "OAUTH_FRONTEND_RETURN_URL", None) or "").strip()
    return v or "http://localhost:3000/connections"


def _make_state_v2(payload: dict[str, Any], *, ttl: int = 600) -> str:
    payload = {**payload, "ts": int(time.time()), "nonce": secrets.token_hex(16), "ttl": ttl}
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{payload_b64}.{_sign_state(payload_b64)}"


def _parse_state_v2(state: str) -> dict[str, Any] | None:
    if not state or "." not in state:
        return None
    payload_b64, sig = state.split(".", 1)
    expected = _sign_state(payload_b64)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        ttl = int(payload.get("ttl") or 600)
        ts = int(payload.get("ts") or 0)
        if ts <= 0 or int(time.time()) - ts > ttl:
            return None
        uid = str(payload.get("uid") or "")
        uuid.UUID(uid)
        return payload
    except Exception:
        return None


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


async def _upsert_connection(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    platform: str,
    access_token: str | None,
    refresh_token: str | None,
    expires_at: datetime | None,
    scopes: str | None,
) -> None:
    existing = (
        await db.execute(
            select(PlatformConnection).where(
                PlatformConnection.user_id == user_id,
                PlatformConnection.platform == platform,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            PlatformConnection(
                user_id=user_id,
                platform=platform,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scopes=scopes,
            )
        )
    else:
        existing.access_token = access_token
        if refresh_token:
            existing.refresh_token = refresh_token
        existing.expires_at = expires_at
        existing.scopes = scopes
        db.add(existing)


async def _upsert_social_account(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    platform: str,
    platform_user_id: str | None = None,
    access_token: str | None,
    refresh_token: str | None,
    expires_at: datetime | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Canonical token store for publishers: `social_accounts` (encrypted).
    Keep `platform_connections` in sync for backward compatibility, but publishers
    should read from `social_accounts`.
    """
    existing = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.user_id == user_id,
                SocialAccount.platform == platform,
            )
        )
    ).scalar_one_or_none()

    meta = metadata or {}
    if existing is None:
        db.add(
            SocialAccount(
                user_id=user_id,
                platform=platform,
                platform_user_id=platform_user_id,
                access_token_encrypted=encrypt_token(access_token),
                refresh_token_encrypted=encrypt_token(refresh_token),
                token_expires_at=expires_at,
                metadata_json=meta or None,
                is_active=True,
                connected_at=datetime.now(timezone.utc),
            )
        )
        return

    existing.platform_user_id = platform_user_id or existing.platform_user_id
    if access_token:
        existing.access_token_encrypted = encrypt_token(access_token)
    if refresh_token:
        existing.refresh_token_encrypted = encrypt_token(refresh_token)
    existing.token_expires_at = expires_at
    existing.is_active = True
    existing.connected_at = existing.connected_at or datetime.now(timezone.utc)
    if meta:
        existing.metadata_json = {**(existing.metadata_json or {}), **meta}
    db.add(existing)


async def _twitter_profile(access_token: str) -> dict[str, Any]:
    # Best-effort; failures should not block connecting.
    url = "https://api.twitter.com/2/users/me"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"authorization": f"Bearer {access_token}"}, params={"user.fields": "profile_image_url,username"})
        if r.status_code != 200:
            return {}
        data = r.json().get("data") or {}
        return {
            "username": data.get("username"),
            "profile_image_url": data.get("profile_image_url"),
            "platform_user_id": data.get("id"),
        }


async def _meta_profile(user_access_token: str) -> dict[str, Any]:
    # Best-effort profile for UX; Graph tokens may not expose email.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(
            f"https://graph.facebook.com/{_meta_graph_version()}/me",
            params={"fields": "id,name", "access_token": user_access_token},
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        return {"platform_user_id": str(data.get("id") or ""), "name": data.get("name")}

def _error_redirect(reason: str = "error") -> RedirectResponse:
    return RedirectResponse(
        url=f"{_frontend_return_url()}?status=error&reason={urllib.parse.quote(reason)}",
        status_code=302,
    )


def _success_redirect(platform: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{_frontend_return_url()}?status=success&platform={platform}",
        status_code=302,
    )


# =============================================================================
# Meta configuration endpoints (page / IG selection)
# =============================================================================


async def _get_social(db: AsyncSession, *, user_id: uuid.UUID, platform: str) -> SocialAccount:
    sa = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.user_id == user_id,
                SocialAccount.platform == platform,
                SocialAccount.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if sa is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{platform} not connected")
    return sa


@router.get("/meta/pages")
async def meta_pages(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    List Facebook Pages available for the connected Meta user token and whether they have
    a linked Instagram Business account. The user can select a page to make publishing deterministic.
    """
    sa = await _get_social(db, user_id=user.id, platform="facebook")
    from app.services.token_crypto import decrypt_token

    user_token = decrypt_token(sa.access_token_encrypted)
    if not user_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Meta access token. Reconnect.")

    pages_url = f"https://graph.facebook.com/{_meta_graph_version()}/me/accounts"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(pages_url, params={"access_token": user_token})
        r.raise_for_status()
        pages = r.json().get("data") or []

        out: list[dict[str, Any]] = []
        for p in pages:
            page_id = str(p.get("id") or "")
            page_name = str(p.get("name") or "")
            page_token = str(p.get("access_token") or "")
            ig_id = None
            if page_id and page_token:
                try:
                    r2 = await client.get(
                        f"https://graph.facebook.com/{_meta_graph_version()}/{page_id}",
                        params={"fields": "instagram_business_account", "access_token": page_token},
                    )
                    if r2.status_code == 200:
                        ig = (r2.json() or {}).get("instagram_business_account") or {}
                        ig_id = str(ig.get("id") or "") or None
                except Exception:
                    ig_id = None

            out.append(
                {
                    "page_id": page_id,
                    "page_name": page_name,
                    "has_instagram_business_account": bool(ig_id),
                    "instagram_business_account_id": ig_id,
                }
            )

    selected_page_id = (sa.metadata_json or {}).get("page_id") if isinstance(sa.metadata_json, dict) else None
    return {"pages": out, "selected_page_id": selected_page_id}


@router.post("/meta/select-page")
async def meta_select_page(
    page_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Persist the user's chosen Facebook Page for deterministic Facebook + Instagram publishing.
    Stores on both `facebook` and `instagram` social_accounts metadata.
    """
    page_id = (page_id or "").strip()
    if not page_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="page_id is required")

    fb = await _get_social(db, user_id=user.id, platform="facebook")
    ig = await _get_social(db, user_id=user.id, platform="instagram")

    # Validate the page exists for this token and capture name + ig business id.
    from app.services.token_crypto import decrypt_token

    user_token = decrypt_token(fb.access_token_encrypted)
    if not user_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Meta access token. Reconnect.")

    pages_url = f"https://graph.facebook.com/{_meta_graph_version()}/me/accounts"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(pages_url, params={"access_token": user_token})
        r.raise_for_status()
        pages = r.json().get("data") or []

        chosen = None
        for p in pages:
            if str(p.get("id") or "") == page_id:
                chosen = p
                break
        if not chosen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="page_id not available for this account")

        page_name = str(chosen.get("name") or "").strip() or None
        page_token = str(chosen.get("access_token") or "").strip() or None

        ig_id = None
        if page_token:
            r2 = await client.get(
                f"https://graph.facebook.com/{_meta_graph_version()}/{page_id}",
                params={"fields": "instagram_business_account", "access_token": page_token},
            )
            if r2.status_code == 200:
                ig_obj = (r2.json() or {}).get("instagram_business_account") or {}
                ig_id = str(ig_obj.get("id") or "") or None

    for sa in (fb, ig):
        meta = sa.metadata_json or {}
        meta["page_id"] = page_id
        if page_name:
            meta["page_name"] = page_name
        if ig_id:
            meta["instagram_business_account_id"] = ig_id
        sa.metadata_json = meta
        db.add(sa)
    await db.commit()
    return {"ok": True, "page_id": page_id, "page_name": page_name, "instagram_business_account_id": ig_id}


# =============================================================================
# Twitter / X
# =============================================================================


def _twitter_creds() -> tuple[str, str, str]:
    cid = (settings.TWITTER_CLIENT_ID or "").strip()
    csec = (settings.TWITTER_CLIENT_SECRET or "").strip()
    redirect = (settings.TWITTER_REDIRECT_URI or "").strip()
    return cid, csec, redirect


@router.get("/twitter/authorize")
async def twitter_authorize(user: User = Depends(get_current_user)) -> dict[str, str]:
    cid, _, redirect = _twitter_creds()
    if not cid or not redirect:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Twitter OAuth not configured (TWITTER_CLIENT_ID/TWITTER_REDIRECT_URI).",
        )

    verifier, challenge = _pkce_pair()
    state = _make_state_v2({"uid": str(user.id), "platform": "twitter", "cv": verifier})

    q = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect,
        "scope": " ".join(TWITTER_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorize_url": f"{TWITTER_AUTH_BASE}?{urllib.parse.urlencode(q)}"}


@router.get("/twitter/callback")
async def twitter_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if error:
        return _error_redirect(error)
    payload = _parse_state_v2(state or "")
    if not payload or not code:
        return _error_redirect("invalid_state")

    cid, csec, redirect = _twitter_creds()
    if not cid or not redirect:
        return _error_redirect("not_configured")

    verifier = str(payload.get("cv") or "")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": cid,
        "redirect_uri": redirect,
        "code_verifier": verifier,
    }
    headers = {"content-type": "application/x-www-form-urlencoded"}
    if csec:
        basic = base64.b64encode(f"{cid}:{csec}".encode("utf-8")).decode("ascii")
        headers["authorization"] = f"Basic {basic}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(TWITTER_TOKEN_URL, data=data, headers=headers)
            r.raise_for_status()
            tok = r.json()
    except Exception:
        return _error_redirect("token_exchange_failed")

    access = str(tok.get("access_token") or "").strip() or None
    refresh = str(tok.get("refresh_token") or "").strip() or None
    expires_in = int(tok.get("expires_in") or 0)
    scope = str(tok.get("scope") or "").strip() or None
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

    await _upsert_connection(
        db,
        user_id=uuid.UUID(str(payload.get("uid"))),
        platform="twitter",
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        scopes=scope,
    )
    # Encrypted canonical store for publishers.
    meta = {}
    try:
        meta = await _twitter_profile(access or "")
    except Exception:
        meta = {}
    await _upsert_social_account(
        db,
        user_id=uuid.UUID(str(payload.get("uid"))),
        platform="twitter",
        platform_user_id=str(meta.get("platform_user_id") or "") or None,
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        metadata={k: v for k, v in meta.items() if v},
    )
    await db.commit()
    return _success_redirect("twitter")


# =============================================================================
# Meta (Facebook + Instagram share one OAuth)
# =============================================================================


def _meta_creds() -> tuple[str, str, str]:
    cid = (settings.META_APP_ID or "").strip()
    csec = (settings.META_APP_SECRET or "").strip()
    redirect = (settings.META_REDIRECT_URI or "").strip()
    return cid, csec, redirect


def _meta_authorize_url(*, user_id: str, platform: str) -> str:
    cid, _, redirect = _meta_creds()
    if not cid or not redirect:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Meta OAuth not configured (META_APP_ID/META_REDIRECT_URI).",
        )
    state = _make_state_v2({"uid": user_id, "platform": platform})
    q = {
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": ",".join(META_SCOPES),
        "state": state,
    }
    return f"{_meta_auth_base()}?{urllib.parse.urlencode(q)}"


@router.get("/facebook/authorize")
async def facebook_authorize(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {"authorize_url": _meta_authorize_url(user_id=str(user.id), platform="facebook")}


@router.get("/instagram/authorize")
async def instagram_authorize(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {"authorize_url": _meta_authorize_url(user_id=str(user.id), platform="instagram")}


@router.get("/meta/callback")
async def meta_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if error:
        return _error_redirect(error)
    payload = _parse_state_v2(state or "")
    if not payload or not code:
        return _error_redirect("invalid_state")

    cid, csec, redirect = _meta_creds()
    if not cid or not csec or not redirect:
        return _error_redirect("not_configured")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                _meta_token_url(),
                params={
                    "client_id": cid,
                    "client_secret": csec,
                    "redirect_uri": redirect,
                    "code": code,
                },
            )
            r.raise_for_status()
            tok = r.json()

            short_token = str(tok.get("access_token") or "").strip()
            if not short_token:
                return _error_redirect("missing_access_token")

            # Exchange for a ~60-day long-lived token.
            r2 = await client.get(
                _meta_token_url(),
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": cid,
                    "client_secret": csec,
                    "fb_exchange_token": short_token,
                },
            )
            long_token = short_token
            long_expires_in = 0
            if r2.status_code == 200:
                ldata = r2.json()
                long_token = str(ldata.get("access_token") or short_token).strip()
                long_expires_in = int(ldata.get("expires_in") or 0)
    except Exception:
        return _error_redirect("token_exchange_failed")

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=long_expires_in)
        if long_expires_in
        else None
    )
    scopes = ",".join(META_SCOPES)
    requested_platform = str(payload.get("platform") or "facebook").lower()
    user_uuid = uuid.UUID(str(payload.get("uid")))

    # Always store BOTH facebook + instagram rows from a single Meta consent so the user
    # only has to authorize once per Meta app, regardless of which button they pressed.
    meta_profile: dict[str, Any] = {}
    try:
        meta_profile = await _meta_profile(long_token)
    except Exception:
        meta_profile = {}
    for plat in ("facebook", "instagram"):
        await _upsert_connection(
            db,
            user_id=user_uuid,
            platform=plat,
            access_token=long_token,
            refresh_token=None,
            expires_at=expires_at,
            scopes=scopes,
        )
        await _upsert_social_account(
            db,
            user_id=user_uuid,
            platform=plat,
            platform_user_id=str(meta_profile.get("platform_user_id") or "") or None,
            access_token=long_token,
            refresh_token=None,
            expires_at=expires_at,
            metadata={k: v for k, v in meta_profile.items() if v},
        )
    await db.commit()
    return _success_redirect(requested_platform)


# =============================================================================
# LinkedIn
# =============================================================================


def _linkedin_dotenv_paths() -> tuple[Path, Path]:
    """`<repo>/.env` then `<backend>/.env` (same layout as `app.core.config.Settings`)."""
    here = Path(__file__).resolve()
    # .../backend/app/api/v1/connections.py
    repo_root = here.parents[4]
    backend_root = here.parents[3]
    return repo_root / ".env", backend_root / ".env"


def _reload_linkedin_dotenv() -> None:
    """
    Re-read `.env` into `os.environ` before LinkedIn OAuth.

    `settings` is built once with `@lru_cache` and can miss values added to `.env` after
    startup. Reloading here lets Connect pick up edits without restarting the server.
    (This module's `settings` import may still be stale; `_linkedin_creds` reads `os.environ` first.)
    """
    from dotenv import load_dotenv

    repo_env, backend_env = _linkedin_dotenv_paths()
    load_dotenv(dotenv_path=repo_env, override=True, encoding="utf-8-sig")
    load_dotenv(dotenv_path=backend_env, override=True, encoding="utf-8-sig")


def _normalize_linkedin_redirect_uri(raw: str) -> str:
    """Strip whitespace and trailing slash so Auth tab URL and .env stay aligned."""
    u = (raw or "").strip()
    return u.rstrip("/") if u else u


def _linkedin_oauth_scope_string() -> str:
    """
    Space-delimited OAuth scopes.

    Override with env `LINKEDIN_OAUTH_SCOPES` (comma- or space-separated), e.g.:
    `openid profile email` while you only have the OpenID product enabled.
    """
    import os

    raw = (os.getenv("LINKEDIN_OAUTH_SCOPES") or "").strip()
    if raw:
        parts = [p for p in raw.replace(",", " ").split() if p]
        return " ".join(parts) if parts else " ".join(LINKEDIN_SCOPES_DEFAULT)
    return " ".join(LINKEDIN_SCOPES_DEFAULT)


def _linkedin_authorize_query(*, client_id: str, redirect_uri: str, state: str, scope: str) -> str:
    """Percent-encode query (LinkedIn samples use %20 between scopes, not +)."""
    from urllib.parse import quote

    def q(s: str) -> str:
        return quote(str(s), safe="")

    return "&".join(
        [
            "response_type=code",
            f"client_id={q(client_id)}",
            f"redirect_uri={q(redirect_uri)}",
            f"state={q(state)}",
            f"scope={q(scope)}",
        ]
    )


def _linkedin_creds() -> tuple[str, str, str]:
    """
    LinkedIn Client ID, Secret, Redirect URI.

    Prefer freshly loaded `os.environ` (see `_reload_linkedin_dotenv`), then fall back to
    the cached Settings object.
    """
    import os

    _reload_linkedin_dotenv()

    def _pick(*keys: str) -> str:
        for k in keys:
            v = (os.getenv(k) or "").strip()
            if v:
                return v
        return ""

    cid = _pick("LINKEDIN_CLIENT_ID") or (str(settings.LINKEDIN_CLIENT_ID or "").strip())
    csec = _pick("LINKEDIN_CLIENT_SECRET") or (str(settings.LINKEDIN_CLIENT_SECRET or "").strip())
    redirect = _normalize_linkedin_redirect_uri(
        _pick("LINKEDIN_REDIRECT_URI") or (str(settings.LINKEDIN_REDIRECT_URI or "").strip())
    )
    return cid, csec, redirect


@router.get("/linkedin/authorize")
async def linkedin_authorize(user: User = Depends(get_current_user)) -> dict[str, str]:
    cid, _, redirect = _linkedin_creds()
    if not cid or not redirect:
        repo_env, backend_env = _linkedin_dotenv_paths()
        logger.warning(
            "linkedin.oauth.authorize_missing_config",
            extra={
                "repo_env_path": str(repo_env),
                "repo_env_exists": repo_env.is_file(),
                "backend_env_path": str(backend_env),
                "backend_env_exists": backend_env.is_file(),
                "has_client_id": bool(cid),
                "has_redirect_uri": bool(redirect),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LinkedIn OAuth is not configured: set non-empty LINKEDIN_CLIENT_ID and "
                "LINKEDIN_REDIRECT_URI in the repository root `.env` or `backend/.env` "
                "(see .env.example). Check the API log line `linkedin.oauth.authorize_missing_config` "
                "for which files were found."
            ),
        )
    state = _make_state_v2({"uid": str(user.id), "platform": "linkedin"})
    scope_str = _linkedin_oauth_scope_string()
    query = _linkedin_authorize_query(
        client_id=cid,
        redirect_uri=redirect,
        state=state,
        scope=scope_str,
    )
    logger.info(
        "linkedin.oauth.authorize_ready",
        extra={"redirect_uri": redirect, "scopes": scope_str},
    )
    return {"authorize_url": f"{LINKEDIN_AUTH_BASE}?{query}"}


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if error:
        return _error_redirect(error)
    payload = _parse_state_v2(state or "")
    if not payload or not code:
        return _error_redirect("invalid_state")

    cid, csec, redirect = _linkedin_creds()
    if not cid or not csec or not redirect:
        return _error_redirect("not_configured")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                LINKEDIN_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect,
                    "client_id": cid,
                    "client_secret": csec,
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            if r.status_code >= 400:
                logger.warning(
                    "linkedin.oauth.token_exchange_http_error",
                    extra={"status_code": r.status_code, "body": (r.text or "")[:800]},
                )
            r.raise_for_status()
            tok = r.json()

            access = str(tok.get("access_token") or "").strip()
            if not access:
                return _error_redirect("missing_access_token")
            refresh = str(tok.get("refresh_token") or "").strip() or None
            try:
                expires_in = int(tok.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            scope = str(tok.get("scope") or "").strip() or None

            # Look up the LinkedIn `sub` (member URN) so we can post on their behalf later.
            person_urn: str | None = None
            try:
                pr = await client.get(
                    LINKEDIN_PROFILE_URL,
                    headers={"authorization": f"Bearer {access}"},
                )
                if pr.status_code == 200:
                    pdata = pr.json()
                    sub = str(pdata.get("sub") or "").strip()
                    if sub:
                        person_urn = f"urn:li:person:{sub}"
            except Exception:
                person_urn = None
    except Exception:
        logger.exception("linkedin.oauth.token_exchange_failed")
        return _error_redirect("token_exchange_failed")

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    )

    user_uuid = uuid.UUID(str(payload.get("uid")))
    # Encode the person URN into `scopes` (it's the only nullable text column on the model).
    scope_blob = scope or ""
    if person_urn:
        scope_blob = f"{scope_blob} urn={person_urn}".strip()

    try:
        await _upsert_connection(
            db,
            user_id=user_uuid,
            platform="linkedin",
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            scopes=scope_blob or None,
        )
        await _upsert_social_account(
            db,
            user_id=user_uuid,
            platform="linkedin",
            platform_user_id=(person_urn.replace("urn:li:person:", "") if person_urn else None),
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            metadata={"person_urn": person_urn} if person_urn else {},
        )
        await db.commit()
    except Exception:
        logger.exception("linkedin.oauth.persist_failed")
        try:
            await db.rollback()
        except Exception:
            pass
        return _error_redirect("persist_failed")

    return _success_redirect("linkedin")

