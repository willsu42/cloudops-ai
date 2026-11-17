from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings, *, application_name: str = "cloudops-api") -> AsyncEngine:
    """Create an async engine with short deadlines so an unhealthy database fails fast.

    No connection is opened here; the first use connects lazily.
    """
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.db_connect_timeout_seconds,
        connect_args={
            # asyncpg: deadline for establishing a connection.
            "timeout": settings.db_connect_timeout_seconds,
            # asyncpg: default deadline for each statement.
            "command_timeout": settings.readiness_timeout_seconds,
            "server_settings": {"application_name": application_name},
        },
    )
