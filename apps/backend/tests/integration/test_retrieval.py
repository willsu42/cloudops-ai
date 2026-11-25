"""Retrieval and the sources API against the pgvector test database.

The mock provider gives meaningless vectors, so these tests check mechanics
(ranking, filters, active sets, thresholds), never retrieval quality. Querying
with a chunk's exact text yields its exact vector, i.e. distance 0.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest import ingest
from app.providers.embeddings import MockEmbeddingProvider
from app.rag.retrieval import RetrievalError, retrieve
from tests.conftest import DATASETS_DIR, ClientFactory, make_settings

pytestmark = pytest.mark.db

V1 = MockEmbeddingProvider("mock-hash-v1")


async def _rows(engine: AsyncEngine, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql), params)]


async def _a_chunk(engine: AsyncEngine, service: str) -> dict[str, Any]:
    rows = await _rows(
        engine,
        "SELECT c.id, c.text, s.embedding_model FROM document_chunks c "
        "JOIN chunk_sets s ON s.id = c.chunk_set_id "
        "JOIN documents d ON d.id = s.document_id "
        "WHERE s.is_active AND d.service_slug = :service "
        "ORDER BY c.index LIMIT 1 OFFSET 1",
        service=service,
    )
    return rows[0]


async def test_exact_text_ranks_its_chunk_first(ingested: AsyncEngine) -> None:
    target = await _a_chunk(ingested, "checkout")
    results = await retrieve(ingested, V1, target["text"], top_k=5, max_distance=2.0)

    assert results[0].chunk_id == target["id"]
    assert results[0].distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].service == "checkout"
    assert [r.distance for r in results] == sorted(r.distance for r in results)
    assert len(results) == 5


async def test_distance_cap_returns_nothing_for_unrelated_text(ingested: AsyncEngine) -> None:
    # Mock vectors of different texts are nearly orthogonal (distance ~1).
    results = await retrieve(
        ingested, V1, "How do I bake sourdough bread?", top_k=5, max_distance=0.8
    )
    assert results == []


async def test_service_filter_applies_before_ranking(ingested: AsyncEngine) -> None:
    checkout = await _a_chunk(ingested, "checkout")
    results = await retrieve(
        ingested, V1, checkout["text"], top_k=50, max_distance=2.0, services=["cart", "payment"]
    )
    cart_and_payment = await _rows(
        ingested,
        "SELECT count(*) AS n FROM document_chunks c JOIN chunk_sets s ON s.id = c.chunk_set_id "
        "JOIN documents d ON d.id = s.document_id "
        "WHERE s.is_active AND d.service_slug IN ('cart', 'payment')",
    )
    assert {r.service for r in results} == {"cart", "payment"}
    assert len(results) == cart_and_payment[0]["n"]  # filtered first, not cut by top_k
    assert checkout["id"] not in {r.chunk_id for r in results}


async def test_only_active_sets_are_searched(ingested: AsyncEngine) -> None:
    old = await _a_chunk(ingested, "checkout")
    v2 = MockEmbeddingProvider("mock-hash-v2")
    await ingest(ingested, DATASETS_DIR, v2)

    results = await retrieve(ingested, v2, old["text"], top_k=50, max_distance=2.0)
    active = {r["id"] for r in await _rows(
        ingested,
        "SELECT c.id FROM document_chunks c JOIN chunk_sets s ON s.id = c.chunk_set_id "
        "WHERE s.is_active",
    )}  # fmt: skip
    assert old["id"] not in {r.chunk_id for r in results}
    assert {r.chunk_id for r in results} <= active
    assert results[0].text == old["text"]  # same passage, from the active v2 set


async def test_model_mismatch_is_an_error(ingested: AsyncEngine) -> None:
    with pytest.raises(RetrievalError, match="re-run ingestion"):
        await retrieve(
            ingested, MockEmbeddingProvider("other-model"), "checkout", top_k=5, max_distance=2.0
        )


async def test_no_ingested_chunks_means_empty(seeded: AsyncEngine) -> None:
    assert await retrieve(seeded, V1, "checkout", top_k=5, max_distance=2.0) == []


@pytest.mark.parametrize(
    ("query", "kwargs", "message"),
    [
        ("   ", {}, "query is empty"),
        ("x" * 2001, {}, "longer than 2000"),
        ("checkout", {"top_k": 0}, "top_k"),
        ("checkout", {"services": ["nope"]}, "unknown service"),
    ],
)
async def test_invalid_requests_are_rejected(
    ingested: AsyncEngine, query: str, kwargs: dict[str, Any], message: str
) -> None:
    args: dict[str, Any] = {"top_k": 5, "max_distance": 2.0, **kwargs}
    with pytest.raises(RetrievalError, match=message):
        await retrieve(ingested, V1, query, **args)


async def test_source_api_returns_the_stored_passage(
    ingested: AsyncEngine, migrated_database: str, make_client: ClientFactory
) -> None:
    target = await _a_chunk(ingested, "payment")
    client = await make_client(make_settings(migrated_database))

    response = await client.get(f"/api/sources/{target['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == str(target["id"])
    assert body["text"] == target["text"]
    assert body["document"]["service"] == "payment"
    assert body["document"]["source_url"].startswith("https://opentelemetry.io/")
    assert body["version"]["source_revision"]
    assert body["version"]["license"] == "CC-BY-4.0"
    assert body["embedding_model"] == "mock-hash-v1"
    assert 1 <= body["start_line"] <= body["end_line"]
    assert "embedding" not in body


async def test_source_api_404s_for_unknown_and_inactive_chunks(
    ingested: AsyncEngine, migrated_database: str, make_client: ClientFactory
) -> None:
    old = await _a_chunk(ingested, "cart")
    await ingest(ingested, DATASETS_DIR, MockEmbeddingProvider("mock-hash-v2"))
    client = await make_client(make_settings(migrated_database))

    for chunk_id in (old["id"], uuid.uuid4()):
        response = await client.get(f"/api/sources/{chunk_id}")
        assert response.status_code == 404
        assert response.json() == {"detail": "source not found"}
    assert (await client.get("/api/sources/not-a-uuid")).status_code == 422
