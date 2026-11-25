"""Exact cosine retrieval over the active chunk sets.

Filters (active set, embedding model, services, distance cap) are applied in the
same query that ranks, so a filtered search never returns fewer results because
of post-filtering. There is no approximate index yet (ADR 003, decision 6).

An empty result is a normal outcome: it means no stored chunk is close enough
to count as evidence, and callers must abstain rather than answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ChunkSet, Document, DocumentChunk, Service
from app.providers.embeddings import EmbeddingProvider, validate_embeddings

MAX_QUERY_CHARS = 2000


class RetrievalError(Exception):
    """Retrieval cannot run as requested; the message is safe to show."""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_key: str
    title: str
    service: str | None
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    text: str
    distance: float


async def _active_models(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.scalars(
            select(ChunkSet.embedding_model).where(ChunkSet.is_active).distinct()
        )
        return set(rows)


async def _check_services(engine: AsyncEngine, services: Sequence[str]) -> None:
    async with engine.connect() as conn:
        known = set(await conn.scalars(select(Service.slug).where(Service.slug.in_(services))))
    unknown = sorted(set(services) - known)
    if unknown:
        raise RetrievalError(f"unknown service(s): {', '.join(unknown)}")


async def retrieve(
    engine: AsyncEngine,
    provider: EmbeddingProvider,
    query: str,
    *,
    top_k: int,
    max_distance: float,
    services: Sequence[str] | None = None,
) -> list[RetrievedChunk]:
    query = query.strip()
    if not query:
        raise RetrievalError("query is empty")
    if len(query) > MAX_QUERY_CHARS:
        raise RetrievalError(f"query is longer than {MAX_QUERY_CHARS} characters")
    if top_k < 1:
        raise RetrievalError("top_k must be at least 1")
    if services:
        await _check_services(engine, services)

    models = await _active_models(engine)
    if not models:
        return []  # nothing ingested yet: no evidence
    if models != {provider.model_id}:
        # Vectors from different models are not comparable.
        raise RetrievalError(
            f"index uses embedding model(s) {sorted(models)}, but queries use "
            f"{provider.model_id!r}; re-run ingestion with the query model"
        )

    vectors = await provider.embed([query])
    validate_embeddings(vectors, expected=1, dim=provider.dim)
    distance = DocumentChunk.embedding.cosine_distance(vectors[0]).label("distance")

    stmt = (
        select(
            DocumentChunk.id,
            Document.source_key,
            Document.title,
            Document.service_slug,
            DocumentChunk.heading_path,
            DocumentChunk.start_line,
            DocumentChunk.end_line,
            DocumentChunk.text,
            distance,
        )
        .join(ChunkSet, ChunkSet.id == DocumentChunk.chunk_set_id)
        .join(Document, Document.id == ChunkSet.document_id)
        .where(ChunkSet.is_active, ChunkSet.embedding_model == provider.model_id)
        .where(distance <= max_distance)
        .order_by(distance, DocumentChunk.id)
        .limit(top_k)
    )
    if services:
        stmt = stmt.where(Document.service_slug.in_(services))

    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [
        RetrievedChunk(
            chunk_id=r.id,
            source_key=r.source_key,
            title=r.title,
            service=r.service_slug,
            heading_path=tuple(r.heading_path),
            start_line=r.start_line,
            end_line=r.end_line,
            text=r.text,
            distance=float(r.distance),
        )
        for r in rows
    ]
