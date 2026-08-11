from __future__ import annotations

from collections.abc import AsyncGenerator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import CHAR, TypeDecorator

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    """
    Cross-dialect UUID type.

    - Postgres: UUID(as_uuid=True)
    - SQLite/others: CHAR(36)
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID

            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # Accept uuid.UUID or str.
        import uuid

        if isinstance(value, uuid.UUID):
            return str(value)
        return str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        import uuid

        return uuid.UUID(str(value))


_engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
if settings.DATABASE_URL.startswith("sqlite"):
    # aiosqlite runs in a threadpool; allow access across threads.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def apply_platform_variant_schema_patches(sync_connection: sa.Connection) -> None:
    """
    Align `platform_variants` with ORM revision 004 (manually_edited, updated_at).

    `Base.metadata.create_all()` does not add columns to existing tables; older SQLite DBs then
    fail with "no such column: platform_variants.manually_edited". This mirrors Alembic 004.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    if "platform_variants" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("platform_variants")}
    dialect_name = sync_connection.dialect.name

    if "manually_edited" not in existing:
        if dialect_name == "sqlite":
            sync_connection.execute(text("ALTER TABLE platform_variants ADD COLUMN manually_edited BOOLEAN NOT NULL DEFAULT 0"))
        else:
            sync_connection.execute(
                text("ALTER TABLE platform_variants ADD COLUMN manually_edited BOOLEAN NOT NULL DEFAULT FALSE")
            )

    if "updated_at" not in existing:
        if dialect_name == "sqlite":
            sync_connection.execute(text("ALTER TABLE platform_variants ADD COLUMN updated_at TIMESTAMP"))
        else:
            sync_connection.execute(text("ALTER TABLE platform_variants ADD COLUMN updated_at TIMESTAMPTZ"))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

