"""Hybrid retrieval over the active chunk sets: vector + full-text, fused by RRF.

Two channels rank candidates independently (ADR 004):

- **Vector:** exact cosine distance (``<=>``), keeping only chunks within
  ``max_distance``. There is no approximate index yet (ADR 003, decision 6).
- **Lexical:** PostgreSQL full-text search on ``document_chunks.search_vector``
  (English stemming, stop words removed). Query terms are OR-ed, so a chunk
  matching some of the terms still counts, and ``ts_rank`` orders the matches.

Reciprocal Rank Fusion combines them: ``score = sum(1 / (RRF_K + rank))`` over
the channels that returned the chunk. RRF uses ranks only, so the two channels'
incomparable raw scores never need calibrating against each other.

All filters (active set, embedding model, services, distance cap) are applied
in the same query that ranks, so filtering never shrinks the top k. Ties are
broken by chunk id, so results are deterministic.

An empty result is a normal outcome: no stored chunk matched well enough to be
evidence, and callers must abstain rather than answer. A non-empty result is
only *candidate* evidence; whether it supports an answer is decided later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import Select, Text, cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ChunkSet, Document, DocumentChunk, Service
from app.providers.embeddings import EmbeddingProvider, validate_embeddings

MAX_QUERY_CHARS = 2000
RRF_K = 60  # the constant from Cormack et al. (2009); damps the weight of top ranks
TEXT_SEARCH_CONFIG = "english"  # must match migration 0003


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
    score: float  # RRF score for hybrid results; channel score for single-channel search
    vector_distance: float | None = None
    lexical_score: float | None = None


def _validate(query: str, top_k: int) -> str:
    query = query.strip()
    if not query:
        raise RetrievalError("query is empty")
    if len(query) > MAX_QUERY_CHARS:
        raise RetrievalError(f"query is longer than {MAX_QUERY_CHARS} characters")
    if top_k < 1:
        raise RetrievalError("top_k must be at least 1")
    return query


async def _check_services(engine: AsyncEngine, services: Sequence[str]) -> None:
    async with engine.connect() as conn:
        known = set(await conn.scalars(select(Service.slug).where(Service.slug.in_(services))))
    unknown = sorted(set(services) - known)
    if unknown:
        raise RetrievalError(f"unknown service(s): {', '.join(unknown)}")


async def _active_models(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.scalars(
            select(ChunkSet.embedding_model).where(ChunkSet.is_active).distinct()
        )
        return set(rows)


def _base(score: Any, services: Sequence[str] | None) -> Select[Any]:
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
            score,
        )
        .join(ChunkSet, ChunkSet.id == DocumentChunk.chunk_set_id)
        .join(Document, Document.id == ChunkSet.document_id)
        .where(ChunkSet.is_active)
    )
    if services:
        stmt = stmt.where(Document.service_slug.in_(services))
    return stmt


async def _run(engine: AsyncEngine, stmt: Select[Any], channel: str) -> list[RetrievedChunk]:
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    out = []
    for r in rows:
        score = float(r.score)
        out.append(
            RetrievedChunk(
                chunk_id=r.id,
                source_key=r.source_key,
                title=r.title,
                service=r.service_slug,
                heading_path=tuple(r.heading_path),
                start_line=r.start_line,
                end_line=r.end_line,
                text=r.text,
                score=score,
                vector_distance=score if channel == "vector" else None,
                lexical_score=score if channel == "lexical" else None,
            )
        )
    return out


async def vector_search(
    engine: AsyncEngine,
    provider: EmbeddingProvider,
    query: str,
    *,
    top_k: int,
    max_distance: float,
    services: Sequence[str] | None = None,
) -> list[RetrievedChunk]:
    """Nearest chunks by cosine distance (lower is closer); ``score`` is the distance."""
    query = _validate(query, top_k)
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
    distance = DocumentChunk.embedding.cosine_distance(vectors[0])
    stmt = (
        _base(distance.label("score"), services)
        .where(ChunkSet.embedding_model == provider.model_id, distance <= max_distance)
        .order_by(distance, DocumentChunk.id)
        .limit(top_k)
    )
    return await _run(engine, stmt, "vector")


async def lexical_search(
    engine: AsyncEngine,
    query: str,
    *,
    top_k: int,
    services: Sequence[str] | None = None,
) -> list[RetrievedChunk]:
    """Full-text matches, any query term (higher ``score`` is better)."""
    query = _validate(query, top_k)
    if services:
        await _check_services(engine, services)

    # plainto_tsquery parses free text safely (no operator syntax) and AND-s the
    # terms; turning '&' into '|' makes a chunk match if it has any of them.
    plain = func.plainto_tsquery(cast(literal(TEXT_SEARCH_CONFIG), REGCONFIG), query)
    tsquery = cast(func.replace(cast(plain, Text), "&", "|"), TSQUERY)
    # Normalization 1 divides by 1 + log(document length), so long chunks do not
    # win just by repeating common terms.
    rank = func.ts_rank(DocumentChunk.search_vector, tsquery, 1)
    stmt = (
        _base(rank.label("score"), services)
        .where(DocumentChunk.search_vector.op("@@")(tsquery))
        .order_by(rank.desc(), DocumentChunk.id)
        .limit(top_k)
    )
    return await _run(engine, stmt, "lexical")


def _first(a: float | None, b: float | None) -> float | None:
    return a if a is not None else b


def fuse(
    channels: Sequence[Sequence[RetrievedChunk]], *, top_k: int, rrf_k: int = RRF_K
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion of already-ranked channel results."""
    scores: dict[uuid.UUID, float] = {}
    merged: dict[uuid.UUID, RetrievedChunk] = {}
    for results in channels:
        for rank, chunk in enumerate(results, start=1):
            cid = chunk.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            seen = merged.setdefault(cid, chunk)
            merged[cid] = replace(
                seen,
                vector_distance=_first(seen.vector_distance, chunk.vector_distance),
                lexical_score=_first(seen.lexical_score, chunk.lexical_score),
            )
    ordered = sorted(merged, key=lambda cid: (-scores[cid], str(cid)))
    return [replace(merged[cid], score=scores[cid]) for cid in ordered[:top_k]]


async def retrieve(
    engine: AsyncEngine,
    provider: EmbeddingProvider,
    query: str,
    *,
    top_k: int,
    max_distance: float,
    candidates: int,
    services: Sequence[str] | None = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval: each channel returns ``candidates`` chunks, fused to ``top_k``."""
    if candidates < top_k:
        raise RetrievalError("candidates must be at least top_k")
    vector = await vector_search(
        engine, provider, query, top_k=candidates, max_distance=max_distance, services=services
    )
    lexical = await lexical_search(engine, query, top_k=candidates, services=services)
    return fuse([vector, lexical], top_k=top_k)
