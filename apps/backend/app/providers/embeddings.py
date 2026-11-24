"""Embedding providers.

Only a deterministic mock exists until AWS access is set up. Bedrock (Titan Text
Embeddings V2, 1024 dimensions) is planned for phase 2G; see ADR 003.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from app.core.config import Settings
from app.db.models import EMBEDDING_DIM


class EmbeddingError(RuntimeError):
    """The provider failed or returned unusable vectors."""


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class MockEmbeddingProvider:
    """Deterministic hash-based vectors, L2-normalized.

    For exercising the pipeline only. The vectors carry no meaning: similar texts
    do not get similar vectors, so retrieval quality measured with this provider
    says nothing about a real model.
    """

    def __init__(self, model_id: str = "mock-hash-v1", dim: int = EMBEDDING_DIM) -> None:
        self._model_id = model_id
        self._dim = dim

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.shake_256(f"{self._model_id}\0{text}".encode()).digest(2 * self._dim)
        raw = [
            int.from_bytes(digest[i : i + 2], "big") / 32767.5 - 1.0
            for i in range(0, len(digest), 2)
        ]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def validate_embeddings(vectors: list[list[float]], *, expected: int, dim: int) -> None:
    if len(vectors) != expected:
        raise EmbeddingError(f"provider returned {len(vectors)} vectors for {expected} texts")
    for i, vector in enumerate(vectors):
        if len(vector) != dim:
            raise EmbeddingError(f"vector {i} has {len(vector)} dimensions, expected {dim}")
        if not all(math.isfinite(x) for x in vector):
            raise EmbeddingError(f"vector {i} contains a non-finite value")


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    # PROVIDER_MODE=aws is rejected during configuration until the Bedrock
    # adapter passes a real smoke test.
    if settings.provider_mode != "mock":
        raise EmbeddingError(f"unsupported provider mode {settings.provider_mode!r}")
    return MockEmbeddingProvider()
