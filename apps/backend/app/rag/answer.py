"""Answer a question from retrieved evidence, or abstain (ADR 005).

1. Retrieve (hybrid). If nothing is retrieved, abstain **without** calling the
   model: there is nothing it could cite.
2. Label the passages S1..Sn and ask the provider for a JSON draft.
3. Ground the draft: keep only claims that cite passages from step 1.
4. Return claims with resolved citations (chunk ids, titles, line ranges), plus
   every retrieved passage, so a reader can check each claim at its source.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import Evidence, LLMProvider
from app.rag.citations import ground
from app.rag.retrieval import RetrievedChunk, retrieve

log = logging.getLogger(__name__)

NO_EVIDENCE = "No passage in the indexed documentation matched this question."


@dataclass(frozen=True)
class Citation:
    chunk_id: uuid.UUID
    label: str
    source_key: str
    title: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Claim:
    text: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Answer:
    abstained: bool
    summary: str
    claims: tuple[Claim, ...]
    limitations: tuple[str, ...]
    retrieved: tuple[RetrievedChunk, ...]
    labels: dict[uuid.UUID, str]
    llm_model: str | None  # None when the model was not called
    embedding_model: str


def _citation(label: str, chunk: RetrievedChunk) -> Citation:
    return Citation(
        chunk_id=chunk.chunk_id,
        label=label,
        source_key=chunk.source_key,
        title=chunk.title,
        heading_path=chunk.heading_path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
    )


async def answer_question(
    engine: AsyncEngine,
    settings: Settings,
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    question: str,
    *,
    services: Sequence[str] | None = None,
) -> Answer:
    retrieved = await retrieve(
        engine,
        embeddings,
        question,
        top_k=settings.retrieval_top_k,
        max_distance=settings.retrieval_max_distance,
        candidates=settings.retrieval_candidates,
        services=services,
    )
    by_label = {f"S{i}": chunk for i, chunk in enumerate(retrieved, start=1)}
    labels = {chunk.chunk_id: label for label, chunk in by_label.items()}

    if not retrieved:
        log.info("ask abstained", extra={"reason": "no_evidence"})
        return Answer(True, "", (), (NO_EVIDENCE,), (), labels, None, embeddings.model_id)

    evidence = [
        Evidence(label=label, title=c.title, heading_path=c.heading_path, text=c.text)
        for label, c in by_label.items()
    ]
    raw = await llm.draft_answer(question, evidence)
    grounded = ground(raw, by_label)

    claims = tuple(
        Claim(c.text, tuple(_citation(label, by_label[label]) for label in c.labels))
        for c in grounded.claims
    )
    log.info(
        "ask answered",
        extra={
            "abstained": grounded.abstained,
            "retrieved": len(retrieved),
            "claims": len(claims),
            "dropped_claims": grounded.dropped_claims,
            "invalid_citations": grounded.invalid_citations,
        },
    )
    return Answer(
        abstained=grounded.abstained,
        summary=grounded.summary,
        claims=claims,
        limitations=grounded.limitations,
        retrieved=tuple(retrieved),
        labels=labels,
        llm_model=llm.model_id,
        embedding_model=embeddings.model_id,
    )
