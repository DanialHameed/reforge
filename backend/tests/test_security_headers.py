"""P-8 security headers + upload e2e regression.

Two concerns are tested together because both ride the FastAPI
middleware stack:

1.  The security-headers middleware must emit the expected header set on
    every response — both API JSON routes (lockdown CSP) and the docs
    routes (permissive CSP that still bans framing).

2.  The new upload validation must end-to-end reject a payload whose
    body does not match the declared ``Content-Type``.

Both run through an isolated in-memory SQLite DB and ``ASGITransport``
so they touch only the in-process FastAPI app — no network, no real
Cloudinary / Redis.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, get_db  # noqa: E402
from app.core.security import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.auth_models import User  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: isolated FastAPI client with a per-test SQLite DB and a stub user
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "sec.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    # The upload endpoint requires an authenticated user. Stub one out
    # with a row that exists in this test DB so any FK works.
    import uuid

    test_user = User(
        id=uuid.uuid4(),
        email="upload-test@example.com",
        hashed_password="x",
    )
    async with sessionmaker() as s:
        s.add(test_user)
        await s.commit()

    async def _override_user() -> User:
        return test_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_emits_lockdown_csp(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'none'" in csp


@pytest.mark.asyncio
async def test_api_route_emits_lockdown_csp(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "default-src 'none'" in resp.headers.get("Content-Security-Policy", "")


@pytest.mark.asyncio
async def test_docs_emits_permissive_csp(client: AsyncClient) -> None:
    resp = await client.get("/docs")
    # Docs returns 200 with the Swagger HTML.
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    # Swagger needs CDN scripts/styles, so default-src is 'self', not 'none'.
    assert "default-src 'self'" in csp
    # But framing is still banned.
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_openapi_emits_permissive_csp(client: AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    # /openapi.json is loaded by Swagger UI, so it gets the docs CSP too.
    assert "default-src 'self'" in csp


@pytest.mark.asyncio
async def test_x_content_type_options_nosniff(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


@pytest.mark.asyncio
async def test_x_frame_options_deny(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers.get("X-Frame-Options") == "DENY"


@pytest.mark.asyncio
async def test_referrer_policy_strict_origin(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_permissions_policy_locks_dangerous_features(client: AsyncClient) -> None:
    resp = await client.get("/health")
    pp = resp.headers.get("Permissions-Policy", "")
    for feature in ("geolocation", "microphone", "camera", "payment", "usb"):
        assert feature in pp
        assert f"{feature}=()" in pp


@pytest.mark.asyncio
async def test_hsts_not_emitted_over_plain_http(client: AsyncClient) -> None:
    # Test client speaks http://, never https://, so HSTS must be absent.
    # (Emitting HSTS over plain HTTP would be a no-op per RFC 6797 but
    # is also a smell that production code may pin localhost.)
    resp = await client.get("/health")
    assert "Strict-Transport-Security" not in resp.headers


# ---------------------------------------------------------------------------
# Upload validation end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_rejects_unknown_mime_with_415(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/content/upload",
        files={"file": ("evil.exe", io.BytesIO(b"MZ\x90\x00"), "application/x-msdownload")},
    )
    assert resp.status_code == 415, resp.text
    assert "Unsupported MIME" in resp.text


@pytest.mark.asyncio
async def test_upload_rejects_html_pretending_to_be_image(client: AsyncClient) -> None:
    # MIME passes the allow-list check (image/png) but the body is
    # HTML; the magic-byte sniff must reject with 400.
    resp = await client.post(
        "/api/v1/content/upload",
        files={"file": ("photo.png", io.BytesIO(b"<html><body>xss</body></html>"), "image/png")},
    )
    assert resp.status_code == 400, resp.text
    assert "does not match" in resp.text.lower()


@pytest.mark.asyncio
async def test_upload_rejects_pe_pretending_to_be_jpeg(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/content/upload",
        files={"file": ("invoice.jpg", io.BytesIO(b"MZ\x90\x00\x03\x00\x00\x00"), "image/jpeg")},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_upload_rejects_missing_content_type(client: AsyncClient) -> None:
    # httpx requires a content-type when sending files; explicitly pass None.
    # The endpoint must reject with 415.
    resp = await client.post(
        "/api/v1/content/upload",
        files={"file": ("x", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "")},
    )
    # Either 415 (mime missing) or 400 (passed through). Both are
    # acceptable rejections — we only care that it does NOT 201.
    assert resp.status_code in (400, 415)


@pytest.mark.asyncio
async def test_upload_accepts_real_png(client: AsyncClient) -> None:
    # The smallest valid PNG. The Cloudinary call will fail (no creds in
    # test env), so the local-fallback branch runs and the row is
    # inserted with status=draft. We only assert this didn't 415/400 —
    # any 2xx means our validation passed without false-positives on a
    # genuine image.
    real_png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    resp = await client.post(
        "/api/v1/content/upload",
        files={"file": ("ok.png", io.BytesIO(real_png), "image/png")},
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Path-traversal hardening on the static-file endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_uploaded_file_rejects_parent_traversal(client: AsyncClient) -> None:
    # FastAPI's path converter strips leading slashes but not all
    # variants of traversal. The endpoint must 404 anything that
    # resolves outside ``uploads/``.
    resp = await client.get("/api/v1/content/uploads/..%2F..%2Fetc%2Fpasswd")
    # We accept either a 404 (path resolved outside) or a 400.
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_get_uploaded_file_404_for_unknown(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/content/uploads/does-not-exist.png")
    assert resp.status_code == 404
