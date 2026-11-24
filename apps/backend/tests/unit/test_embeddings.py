"""Mock embedding provider and vector validation."""

from __future__ import annotations

import math

import pytest

from app.db.models import EMBEDDING_DIM
from app.providers.embeddings import EmbeddingError, MockEmbeddingProvider, validate_embeddings


async def test_mock_is_deterministic_normalized_and_sized() -> None:
    provider = MockEmbeddingProvider()
    first = await provider.embed(["checkout calls payment", "cart uses valkey"])
    second = await MockEmbeddingProvider().embed(["checkout calls payment", "cart uses valkey"])

    assert first == second
    assert provider.dim == EMBEDDING_DIM == 1024
    for vector in first:
        assert len(vector) == EMBEDDING_DIM
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)
    assert first[0] != first[1]


async def test_mock_model_id_changes_vectors() -> None:
    v1 = await MockEmbeddingProvider("mock-hash-v1").embed(["same text"])
    v2 = await MockEmbeddingProvider("mock-hash-v2").embed(["same text"])
    assert v1 != v2


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ([[0.0] * 4], "1 vectors for 2 texts"),
        ([[0.0] * 4, [0.0] * 3], "vector 1 has 3 dimensions"),
        ([[0.0] * 4, [0.0, math.nan, 0.0, 0.0]], "non-finite"),
        ([[0.0] * 4, [math.inf, 0.0, 0.0, 0.0]], "non-finite"),
    ],
)
def test_validate_rejects_bad_vectors(vectors: list[list[float]], message: str) -> None:
    with pytest.raises(EmbeddingError, match=message):
        validate_embeddings(vectors, expected=2, dim=4)


def test_validate_accepts_good_vectors() -> None:
    validate_embeddings([[0.5] * 4, [0.1] * 4], expected=2, dim=4)
