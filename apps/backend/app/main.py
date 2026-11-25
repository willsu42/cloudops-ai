"""FastAPI application factory.

Run with: uvicorn app.main:create_app --factory
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import ask, health, sources
from app.core.config import Settings, load_settings
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db import create_engine
from app.providers.embeddings import get_embedding_provider
from app.providers.llm import get_llm_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="CloudOps AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.embedding_provider = get_embedding_provider(settings)
    app.state.llm_provider = get_llm_provider(settings)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(ask.router)
    return app
