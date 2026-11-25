"""Retrieval and the sources API against the pgvector test database.

The mock provider gives meaningless vectors, so these tests check mechanics
(ranking, filters, active sets, thresholds, fusion), never retrieval quality.
Querying with a chunk's exact text yields its exact vector, i.e. distance 0.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest import ingest
from app.providers.embeddings import MockEmbeddingProvider
from app.rag.retrieval import RetrievalError, lexical_search, retrieve, vector_search
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
    results = await vector_search(ingested, V1, target["text"], top_k=5, max_distance=2.0)

    assert results[0].chunk_id == target["id"]
    assert results[0].vector_distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].service == "checkout"
    assert [r.score for r in results] == sorted(r.score for r in results)
    assert len(results) == 5


async def test_distance_cap_returns_nothing_for_unrelated_text(ingested: AsyncEngine) -> None:
    # Mock vectors of different texts are nearly orthogonal (distance ~1).
    results = await vector_search(
        ingested, V1, "How do I bake sourdough bread?", top_k=5, max_distance=0.8
    )
    assert results == []


async def test_service_filter_applies_before_ranking(ingested: AsyncEngine) -> None:
    checkout = await _a_chunk(ingested, "checkout")
    results = await vector_search(
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

    results = await vector_search(ingested, v2, old["text"], top_k=50, max_distance=2.0)
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
        await vector_search(
            ingested, MockEmbeddingProvider("other-model"), "checkout", top_k=5, max_distance=2.0
        )


async def test_no_ingested_chunks_means_empty(seeded: AsyncEngine) -> None:
    assert await vector_search(seeded, V1, "checkout", top_k=5, max_distance=2.0) == []


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
        await vector_search(ingested, V1, query, **args)


async def test_search_vector_is_generated_for_every_chunk(ingested: AsyncEngine) -> None:
    rows = await _rows(
        ingested,
        "SELECT count(*) AS n, count(*) FILTER (WHERE search_vector IS NULL "
        "OR search_vector = ''::tsvector) AS empty FROM document_chunks",
    )
    assert rows[0]["n"] > 0
    assert rows[0]["empty"] == 0


async def test_lexical_search_matches_stemmed_terms_any_of(ingested: AsyncEngine) -> None:
    # "Valkey" appears only in cart-related text; the made-up word matches nothing,
    # and OR semantics keep the Valkey matches.
    results = await lexical_search(ingested, "valkey zyxwvut", top_k=5)
    assert results
    assert all("valkey" in r.text.lower() for r in results)
    assert all(r.lexical_score is not None and r.lexical_score > 0 for r in results)
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


async def test_lexical_search_treats_operators_as_plain_text(ingested: AsyncEngine) -> None:
    # tsquery syntax in user input must not raise or change the query's meaning.
    results = await lexical_search(ingested, "checkout & !(payment | ':*", top_k=3)
    assert len(results) == 3
    assert await lexical_search(ingested, "the and of", top_k=3) == []  # only stop words


async def test_lexical_search_honours_service_filter_and_active_sets(
    ingested: AsyncEngine,
) -> None:
    old = await lexical_search(ingested, "checkout order", top_k=50, services=["kafka"])
    assert old
    assert {r.service for r in old} == {"kafka"}
    await ingest(ingested, DATASETS_DIR, MockEmbeddingProvider("mock-hash-v2"))
    new = await lexical_search(ingested, "checkout order", top_k=50, services=["kafka"])
    assert {r.chunk_id for r in new}.isdisjoint({r.chunk_id for r in old})
    assert [r.text for r in new] == [r.text for r in old]  # same passages, active set only


async def test_hybrid_puts_a_chunk_found_by_both_channels_first(ingested: AsyncEngine) -> None:
    target = await _a_chunk(ingested, "currency")
    results = await retrieve(ingested, V1, target["text"], top_k=5, max_distance=0.8, candidates=20)
    assert results[0].chunk_id == target["id"]
    assert results[0].vector_distance == pytest.approx(0.0, abs=1e-6)
    assert results[0].lexical_score is not None
    assert results[0].score > results[1].score
    assert len(results) == 5


async def test_hybrid_falls_back_to_lexical_when_vectors_find_nothing(
    ingested: AsyncEngine,
) -> None:
    question = "Where does the cart service keep shopping cart data?"
    hybrid = await retrieve(ingested, V1, question, top_k=5, max_distance=0.8, candidates=20)
    lexical = await lexical_search(ingested, question, top_k=5)
    assert await vector_search(ingested, V1, question, top_k=5, max_distance=0.8) == []
    assert [r.chunk_id for r in hybrid] == [r.chunk_id for r in lexical]
    assert all(r.vector_distance is None for r in hybrid)


async def test_hybrid_returns_nothing_without_any_match(ingested: AsyncEngine) -> None:
    results = await retrieve(
        ingested, V1, "zyxwvut qqqqq", top_k=5, max_distance=0.8, candidates=20
    )
    assert results == []


async def test_hybrid_requires_enough_candidates(ingested: AsyncEngine) -> None:
    with pytest.raises(RetrievalError, match="candidates"):
        await retrieve(ingested, V1, "checkout", top_k=10, max_distance=0.8, candidates=5)


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
