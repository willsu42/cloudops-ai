"""Tests against the dedicated PostgreSQL/pgvector test database."""

from __future__ import annotations

import time
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.seed import load_manifest, seed, version_id
from tests.conftest import DATASETS_DIR, ClientFactory, make_settings
from tests.support.tcp import ToggleProxy

pytestmark = pytest.mark.db


async def test_ready_with_database_and_vector_extension(
    migrated_database: str, make_client: ClientFactory
) -> None:
    client = await make_client(make_settings(migrated_database))
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "vector_extension": "ok"},
    }


async def test_readiness_fails_fast_during_outage_and_recovers(
    migrated_database: str, make_client: ClientFactory
) -> None:
    url = make_url(migrated_database)
    proxy = ToggleProxy(url.host or "127.0.0.1", url.port or 5432)
    await proxy.start()
    proxied = url.set(host="127.0.0.1", port=proxy.port).render_as_string(hide_password=False)
    try:
        client = await make_client(make_settings(proxied, readiness_timeout_seconds=1.0))
        assert (await client.get("/health/ready")).status_code == 200

        proxy.set_down()
        start = time.perf_counter()
        down = await client.get("/health/ready")
        assert down.status_code == 503
        assert time.perf_counter() - start < 2.5
        assert (await client.get("/health/live")).status_code == 200

        proxy.set_up()
        assert (await client.get("/health/ready")).status_code == 200
    finally:
        await proxy.close()


async def _snapshot(engine: AsyncEngine) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    async with engine.connect() as conn:
        for table, key in (
            ("services", "slug"),
            ("documents", "id"),
            ("document_versions", "id"),
        ):
            rows = await conn.execute(text(f"SELECT * FROM {table} ORDER BY {key}"))  # noqa: S608
            out[table] = [dict(r._mapping) for r in rows]
    return out


async def test_seed_is_repeatable(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE services, documents, document_versions CASCADE"))

    await seed(db_engine, DATASETS_DIR)
    first = await _snapshot(db_engine)
    await seed(db_engine, DATASETS_DIR)
    second = await _snapshot(db_engine)

    assert first == second  # same IDs, same values, same timestamps, no duplicates
    manifest = load_manifest(DATASETS_DIR)
    assert len(first["services"]) == len(manifest.services)
    assert len(first["documents"]) == len(manifest.documents)
    assert len(first["document_versions"]) == len(manifest.documents)

    checkout = next(d for d in manifest.documents if d.source_key == "otel-demo-service-checkout")
    expected_id = version_id(checkout.source_key, checkout.source_revision)
    assert expected_id in {row["id"] for row in first["document_versions"]}
    assert all(not row["modified"] for row in first["document_versions"])
