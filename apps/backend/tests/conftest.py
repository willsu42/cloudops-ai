"""Shared fixtures.

Database tests use TEST_DATABASE_URL, which must point at a dedicated database
whose name ends in `_test` (see docker-compose.test.yml). The development
database is never touched. When TEST_DATABASE_URL is unset, database tests are
skipped, unless REQUIRE_DB_TESTS=1 (set in CI), in which case they fail.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import REPO_ROOT, Settings, load_settings
from app.ingest import ingest
from app.main import create_app
from app.providers.embeddings import MockEmbeddingProvider
from app.seed import seed

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets"


def make_settings(database_url: str, **overrides: object) -> Settings:
    return load_settings(_env_file=None, database_url=database_url, **overrides)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        message = "TEST_DATABASE_URL is not set; start it with `make test-db`"
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"Refusing to run against {database!r}: test database names must end in _test")
    return url


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA public")
    await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> Iterator[str]:
    """Reset the dedicated test database and migrate it to head, once per session."""
    asyncio.run(_reset_schema(test_database_url))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = test_database_url
    command.upgrade(config, "head")
    yield test_database_url


@pytest.fixture
async def db_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(db_engine: AsyncEngine) -> AsyncEngine:
    """Empty every table, then seed services and document metadata."""
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE services, documents, document_versions, ingestion_runs CASCADE")
        )
    await seed(db_engine, DATASETS_DIR)
    return db_engine


@pytest.fixture
async def ingested(seeded: AsyncEngine) -> AsyncEngine:
    """Seeded database with every corpus document ingested by the mock provider."""
    report = await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider())
    assert report.status == "succeeded"
    return seeded


ClientFactory = Callable[[Settings], Awaitable[AsyncClient]]


@pytest.fixture
async def make_client() -> AsyncIterator[ClientFactory]:
    """Build an app + HTTP client for given settings; dispose both afterwards."""
    created: list[tuple[FastAPI, AsyncClient]] = []

    async def _make(settings: Settings) -> AsyncClient:
        app = create_app(settings)
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        created.append((app, client))
        return client

    yield _make
    for app, client in created:
        await client.aclose()
        await app.state.engine.dispose()
