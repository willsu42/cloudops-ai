"""POST /api/ask against the pgvector test database with mock providers."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.providers.embeddings import MockEmbeddingProvider
from app.providers.llm import Evidence, MockLLMProvider
from app.rag.citations import INVALID_DRAFT
from tests.conftest import ClientFactory, make_settings

pytestmark = pytest.mark.db

ANSWERABLE = "Where does the cart service keep shopping cart data?"
INSUFFICIENT = "What is the current p99 latency of checkout's PlaceOrder call?"


class SpyLLM(MockLLMProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def draft_answer(self, question: str, evidence: Sequence[Evidence]) -> str:
        self.calls += 1
        return await super().draft_answer(question, evidence)


class ScriptedLLM:
    model_id = "scripted"

    def __init__(self, output: str | Exception) -> None:
        self.output = output

    async def draft_answer(self, question: str, evidence: Sequence[Evidence]) -> str:
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture
async def client(
    ingested: AsyncEngine, migrated_database: str, make_client: ClientFactory
) -> AsyncClient:
    return await make_client(make_settings(migrated_database))


def _set_llm(client: AsyncClient, llm: object) -> None:
    client._transport.app.state.llm_provider = llm  # type: ignore[attr-defined]


async def test_answer_claims_are_quotes_of_their_cited_sources(client: AsyncClient) -> None:
    response = await client.post("/api/ask", json={"question": ANSWERABLE})
    assert response.status_code == 200
    body = response.json()

    assert body["abstained"] is False
    assert body["claims"]
    assert body["llm_model"] == "mock-extractive-v1"
    assert body["embedding_model"] == "mock-hash-v1"
    retrieved = {r["chunk_id"]: r["label"] for r in body["retrieved"]}
    for claim in body["claims"]:
        assert claim["citations"]
        for citation in claim["citations"]:
            assert retrieved[citation["chunk_id"]] == citation["label"]
            source = await client.get(f"/api/sources/{citation['chunk_id']}")
            assert source.status_code == 200
            assert _normalize(claim["text"]) in _normalize(source.json()["text"])
    cited = {c["source_key"] for claim in body["claims"] for c in claim["citations"]}
    assert "otel-demo-service-cart" in cited


async def test_question_without_support_abstains(client: AsyncClient) -> None:
    body = (await client.post("/api/ask", json={"question": INSUFFICIENT})).json()
    assert body["abstained"] is True
    assert body["claims"] == []
    assert body["summary"] == ""
    assert body["limitations"]
    assert body["retrieved"]  # candidates existed; none supported an answer


async def test_no_retrieval_abstains_without_calling_the_model(client: AsyncClient) -> None:
    spy = SpyLLM()
    _set_llm(client, spy)
    body = (await client.post("/api/ask", json={"question": "zyxwvut qqqqq"})).json()
    assert spy.calls == 0
    assert body["abstained"] is True
    assert body["llm_model"] is None
    assert body["retrieved"] == []
    assert body["limitations"] == ["No passage in the indexed documentation matched this question."]


async def test_fabricated_citations_are_removed(client: AsyncClient) -> None:
    _set_llm(
        client,
        ScriptedLLM(
            json.dumps(
                {
                    "abstained": False,
                    "summary": "Mixed draft.",
                    "claims": [
                        {"text": "Grounded claim.", "citations": ["S1"]},
                        {"text": "Cites a passage that was not retrieved.", "citations": ["S42"]},
                        {"text": "Cites nothing.", "citations": []},
                    ],
                    "limitations": [],
                }
            )
        ),
    )
    body = (await client.post("/api/ask", json={"question": ANSWERABLE})).json()
    assert body["abstained"] is False
    assert [c["text"] for c in body["claims"]] == ["Grounded claim."]
    assert body["claims"][0]["citations"][0]["chunk_id"] == body["retrieved"][0]["chunk_id"]
    assert body["limitations"] == ["2 claim(s) without a valid citation were removed."]


async def test_malformed_draft_abstains(client: AsyncClient) -> None:
    _set_llm(client, ScriptedLLM("Sure! Here is the answer: checkout calls payment."))
    body = (await client.post("/api/ask", json={"question": ANSWERABLE})).json()
    assert body["abstained"] is True
    assert body["claims"] == []
    assert body["limitations"] == [INVALID_DRAFT]


async def test_provider_failure_is_a_502_without_details(client: AsyncClient) -> None:
    _set_llm(client, ScriptedLLM(RuntimeError("secret upstream detail")))
    response = await client.post("/api/ask", json={"question": ANSWERABLE})
    assert response.status_code == 502
    assert response.json() == {"detail": "answer generation failed"}


async def test_index_built_with_another_model_is_a_503(client: AsyncClient) -> None:
    client._transport.app.state.embedding_provider = MockEmbeddingProvider("other")  # type: ignore[attr-defined]
    response = await client.post("/api/ask", json={"question": ANSWERABLE})
    assert response.status_code == 503
    assert response.json() == {"detail": "index not ready"}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"question": ""}, 422),
        ({"question": "x" * 1001}, 422),
        ({"question": "ok", "extra": 1}, 422),
        ({"question": "   "}, 400),
        ({"question": "cart", "services": ["nope"]}, 400),
    ],
)
async def test_invalid_requests(
    client: AsyncClient, payload: dict[str, object], status: int
) -> None:
    assert (await client.post("/api/ask", json=payload)).status_code == status


async def test_service_filter_limits_citations(client: AsyncClient) -> None:
    body = (
        await client.post("/api/ask", json={"question": ANSWERABLE, "services": ["payment"]})
    ).json()
    assert {r["service"] for r in body["retrieved"]} <= {"payment"}
