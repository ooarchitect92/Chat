from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from northstar_api.config import Settings, get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


tenant_context: ContextVar[str | None] = ContextVar("database_tenant_id", default=None)


def build_engine(settings: Settings | None = None, *, apply_runtime_role: bool = False) -> AsyncEngine:
    config = settings or get_settings()
    kwargs: dict[str, Any] = {"echo": config.database_echo, "pool_pre_ping": True}
    if not config.database_url.startswith("sqlite"):
        kwargs.update(pool_size=config.database_pool_size, max_overflow=config.database_max_overflow)
    selected = create_async_engine(config.database_url, **kwargs)
    if apply_runtime_role and config.is_postgres:

        @event.listens_for(selected.sync_engine, "begin")
        def apply_postgres_security_context(connection: Any) -> None:
            connection.exec_driver_sql(f'SET LOCAL ROLE "{config.database_runtime_role}"')
            tenant_id = tenant_context.get()
            if tenant_id:
                connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": tenant_id},
                )

    return selected


settings = get_settings()
engine = build_engine(
    settings,
    apply_runtime_role=(
        settings.is_postgres and not settings.app_auto_create_schema and settings.database_apply_runtime_role
    ),
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    context_token = tenant_context.set(None)
    try:
        async with SessionFactory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
    finally:
        tenant_context.reset(context_token)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    tenant_context.set(str(tenant_id))
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
        )


async def initialize_schema(db_engine: AsyncEngine | None = None) -> None:
    selected = db_engine or engine
    async with selected.begin() as connection:
        if selected.dialect.name == "postgresql":
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)


async def close_database() -> None:
    await engine.dispose()


@event.listens_for(Engine, "connect")
def configure_sqlite(connection: Any, _: Any) -> None:
    if connection.__class__.__module__.startswith("sqlite3"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
