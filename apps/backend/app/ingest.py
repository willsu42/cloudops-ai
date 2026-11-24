"""Chunk, embed, and store the corpus documents listed in the manifest.

Run with: python -m app.ingest  (after `python -m app.seed`)

For each document:

1. Only Markdown (``.md``) is accepted. Other formats, PDF included, fail with
   an explicit "unsupported format" error (ADR 003).
2. The local file must match a seeded ``document_versions`` row by content
   hash. Otherwise nothing is stored, so stored chunks always come from the
   exact recorded version.
3. The chunk set id is derived from (version, chunker version, embedding model).
   If that set is already active, the document is skipped. If it exists but is
   inactive, it is re-activated without re-embedding.
4. Otherwise the document is chunked and embedded. In one transaction the new
   set and its chunks are written, the document's previous active set is
   deactivated, and the new set is activated. On any failure the transaction
   rolls back and the previous active set stays in place.

Documents are processed independently: one failure does not stop the others.
The run is recorded in ``ingestion_runs``; the command exits 1 if any document
failed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import load_settings
from app.core.logging import configure_logging
from app.db import create_engine
from app.db.models import EMBEDDING_DIM, ChunkSet, DocumentChunk, DocumentVersion, IngestionRun
from app.providers.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    get_embedding_provider,
    validate_embeddings,
)
from app.rag.chunking import CHUNKER_VERSION, chunk_markdown
from app.seed import ID_NAMESPACE, ManifestDocument, document_id, load_manifest

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = frozenset({".md"})
EMBED_BATCH_SIZE = 32
MAX_ERROR_CHARS = 2000
MAX_DOCUMENT_ERROR_CHARS = 300

Outcome = Literal["ingested", "reactivated", "skipped", "failed"]


class IngestError(Exception):
    """A document cannot be ingested; the message is safe to store and show."""


@dataclass(frozen=True)
class DocumentResult:
    source_key: str
    outcome: Outcome
    chunks_written: int = 0
    error: str | None = None


@dataclass
class IngestReport:
    run_id: uuid.UUID
    results: list[DocumentResult] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def status(self) -> Literal["succeeded", "failed"]:
        return "failed" if self.count("failed") else "succeeded"


def chunk_set_id(version: uuid.UUID, chunker_version: str, embedding_model: str) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"chunk-set:{version}:{chunker_version}:{embedding_model}")


def chunk_id(set_id: uuid.UUID, index: int) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"chunk:{set_id}:{index}")


def _read_markdown(doc: ManifestDocument, repo_root: Path) -> bytes:
    path = repo_root / doc.local_path
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise IngestError(
            f"unsupported format {path.suffix or '(none)'!r}: only Markdown (.md) is ingested"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise IngestError(f"cannot read {doc.local_path}: {exc.strerror}") from exc


async def _find_version(conn: AsyncConnection, doc: ManifestDocument, sha256: str) -> uuid.UUID:
    version = await conn.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == document_id(doc.source_key),
            DocumentVersion.content_sha256 == sha256,
        )
    )
    if version is None:
        raise IngestError(
            "no seeded document version matches this file's content; "
            "run `make seed` first, or restore the file"
        )
    return version


async def _activate(conn: AsyncConnection, doc_id: uuid.UUID, set_id: uuid.UUID) -> None:
    # Deactivate first: a partial unique index allows one active set per document.
    await conn.execute(
        update(ChunkSet)
        .where(ChunkSet.document_id == doc_id, ChunkSet.is_active, ChunkSet.id != set_id)
        .values(is_active=False)
    )
    await conn.execute(
        update(ChunkSet)
        .where(ChunkSet.id == set_id)
        .values(is_active=True, activated_at=func.now())
    )


async def _embed(provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        try:
            out = await provider.embed(batch)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"embedding provider failed: {type(exc).__name__}") from exc
        validate_embeddings(out, expected=len(batch), dim=provider.dim)
        vectors.extend(out)
    return vectors


async def _ingest_document(
    engine: AsyncEngine,
    doc: ManifestDocument,
    repo_root: Path,
    provider: EmbeddingProvider,
    run_id: uuid.UUID,
) -> DocumentResult:
    content = _read_markdown(doc, repo_root)
    doc_id = document_id(doc.source_key)
    async with engine.connect() as conn:
        version = await _find_version(conn, doc, hashlib.sha256(content).hexdigest())
        set_id = chunk_set_id(version, CHUNKER_VERSION, provider.model_id)
        existing = await conn.scalar(select(ChunkSet.is_active).where(ChunkSet.id == set_id))

    if existing is True:
        return DocumentResult(doc.source_key, "skipped")
    if existing is False:
        async with engine.begin() as conn:
            await _activate(conn, doc_id, set_id)
        return DocumentResult(doc.source_key, "reactivated")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError("file is not valid UTF-8") from exc
    chunks = chunk_markdown(text)
    if not chunks:
        raise IngestError("document produced no chunks")
    vectors = await _embed(provider, [c.text for c in chunks])

    async with engine.begin() as conn:
        await conn.execute(
            insert(ChunkSet).values(
                id=set_id,
                document_id=doc_id,
                document_version_id=version,
                chunker_version=CHUNKER_VERSION,
                embedding_model=provider.model_id,
                embedding_dim=provider.dim,
                chunk_count=len(chunks),
                ingestion_run_id=run_id,
                is_active=False,
            )
        )
        await conn.execute(
            insert(DocumentChunk),
            [
                {
                    "id": chunk_id(set_id, c.index),
                    "chunk_set_id": set_id,
                    "document_version_id": version,
                    "index": c.index,
                    "text": c.text,
                    "heading_path": list(c.heading_path),
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "token_estimate": c.token_estimate,
                    "content_sha256": c.content_sha256,
                    "embedding": vector,
                }
                for c, vector in zip(chunks, vectors, strict=True)
            ],
        )
        await _activate(conn, doc_id, set_id)
    return DocumentResult(doc.source_key, "ingested", chunks_written=len(chunks))


def _describe(exc: Exception) -> str:
    message = str(exc) if isinstance(exc, IngestError | EmbeddingError) else type(exc).__name__
    return message[:MAX_DOCUMENT_ERROR_CHARS]


async def ingest(
    engine: AsyncEngine, datasets_dir: Path, provider: EmbeddingProvider
) -> IngestReport:
    if provider.dim != EMBEDDING_DIM:
        raise EmbeddingError(
            f"provider {provider.model_id!r} produces {provider.dim}-dimensional vectors; "
            f"the database stores {EMBEDDING_DIM} (see ADR 003)"
        )
    manifest = load_manifest(datasets_dir)
    repo_root = datasets_dir.parent

    report = IngestReport(run_id=uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            insert(IngestionRun).values(
                id=report.run_id,
                status="running",
                chunker_version=CHUNKER_VERSION,
                embedding_model=provider.model_id,
                embedding_dim=provider.dim,
                documents_total=len(manifest.documents),
            )
        )

    for doc in manifest.documents:
        try:
            result = await _ingest_document(engine, doc, repo_root, provider, report.run_id)
        except Exception as exc:
            result = DocumentResult(doc.source_key, "failed", error=_describe(exc))
            log.warning(
                "document failed", extra={"source_key": doc.source_key, "error": result.error}
            )
        report.results.append(result)

    errors = "; ".join(f"{r.source_key}: {r.error}" for r in report.results if r.error)
    async with engine.begin() as conn:
        await conn.execute(
            update(IngestionRun)
            .where(IngestionRun.id == report.run_id)
            .values(
                status=report.status,
                documents_ingested=report.count("ingested") + report.count("reactivated"),
                documents_skipped=report.count("skipped"),
                documents_failed=report.count("failed"),
                chunks_written=sum(r.chunks_written for r in report.results),
                error=errors[:MAX_ERROR_CHARS] or None,
                finished_at=func.now(),
            )
        )
    log.info(
        "ingest complete",
        extra={
            "run_id": str(report.run_id),
            "status": report.status,
            **{o: report.count(o) for o in ("ingested", "reactivated", "skipped", "failed")},
        },
    )
    return report


async def _main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings, application_name="cloudops-ingest")
    try:
        report = await ingest(engine, settings.datasets_dir, get_embedding_provider(settings))
    finally:
        await engine.dispose()
    for r in report.results:
        detail = f" ({r.chunks_written} chunks)" if r.chunks_written else ""
        suffix = f": {r.error}" if r.error else ""
        print(f"{r.outcome:<11} {r.source_key}{detail}{suffix}")
    print(f"run {report.run_id}: {report.status}")
    return 0 if report.status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
