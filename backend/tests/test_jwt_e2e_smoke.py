"""End-to-end smoke test for the B-1 fix.

Boots the real FastAPI app, registers a user via the public auth route, and
verifies that the issued access token:

* is signed with ``settings.jwt_signing_secret`` (i.e. the value resolved
  from ``SECRET_KEY``/``JWT_SECRET_KEY`` rather than the literal default), and
* unlocks a protected endpoint (``GET /api/v1/auth/me``).

This guards the full request → middleware → dependency → handler chain, not
just the security primitives in isolation.

Test isolation:
* The DB layer is overridden via ``app.dependency_overrides[get_db]`` and a
  test-local async engine bound to a unique tmp file. We do NOT rely on the
  module-level ``app.core.database.engine`` or ``SessionLocal`` because their
  binding is decided at the moment ``app.core.database`` is first imported,
  which happens during pytest collection of any earlier test file.
* The settings singleton is mutated for the test only and restored at exit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_SECRET = "e2e-smoke-secret-key-1234567890abcdef-1234567890"


@pytest_asyncio.fixture
async def isolated_app(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Yield an httpx client bound to the real FastAPI app with an isolated DB.

    Each test gets:
    * its own sqlite file (tmp_path),
    * its own async engine + sessionmaker,
    * an override of the ``get_db`` dependency that hands out sessions from
      the isolated engine,
    * a ``settings.SECRET_KEY`` override so the JWT is signed with a known
      value we can decode in assertions.
    """
    db_path = tmp_path / "smoke.db"
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", future=True
    )
    test_sessionmaker = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            yield session

    original_secret_key = settings.SECRET_KEY
    original_jwt_secret_key = settings.JWT_SECRET_KEY
    settings.SECRET_KEY = TEST_SECRET
    settings.JWT_SECRET_KEY = "change-me"

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        settings.SECRET_KEY = original_secret_key
        settings.JWT_SECRET_KEY = original_jwt_secret_key
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_register_login_and_authenticated_request_use_real_secret(
    isolated_app: AsyncClient,
) -> None:
    register_resp = await isolated_app.post(
        "/api/v1/auth/register",
        json={
            "email": "smoke@example.com",
            "password": "VerySafePassword!1",
            "display_name": "Smoke",
        },
    )
    assert register_resp.status_code == 201, register_resp.text
    body = register_resp.json()
    token = body["access_token"]
    assert isinstance(token, str) and token.count(".") == 2

    decoded = jwt.decode(token, TEST_SECRET, algorithms=[settings.JWT_ALGORITHM])
    assert decoded["email"] == "smoke@example.com"

    with pytest.raises(Exception):
        jwt.decode(token, "change-me", algorithms=[settings.JWT_ALGORITHM])

    me_resp = await isolated_app.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200, me_resp.text
    assert me_resp.json()["email"] == "smoke@example.com"

    forged_token = jwt.encode(
        {"sub": "attacker", "email": "evil@example.com"},
        "change-me",
        algorithm=settings.JWT_ALGORITHM,
    )
    forged_resp = await isolated_app.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged_token}"}
    )
    assert forged_resp.status_code == 401, (
        "A token forged with the legacy default placeholder must be rejected"
    )
