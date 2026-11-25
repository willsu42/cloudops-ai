"""Resolve a cited chunk to its exact stored passage and provenance.

Only chunks in an active chunk set resolve; anything else is a 404, so a
citation can never point at superseded or unknown text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ChunkSet, Document, DocumentChunk, DocumentVersion

router = APIRouter(prefix="/api/sources", tags=["sources"])
log = logging.getLogger(__name__)


class SourceDocument(BaseModel):
    source_key: str
    title: str
    kind: str
    service: str | None
    source_url: str


class SourceVersion(BaseModel):
    source_repository: str
    source_revision: str
    source_file_url: str
    content_sha256: str
    retrieved_on: date
    license: str
    attribution: str


class SourceChunk(BaseModel):
    chunk_id: uuid.UUID
    heading_path: list[str]
    start_line: int
    end_line: int
    text: str
    chunker_version: str
    embedding_model: str
    document: SourceDocument
    version: SourceVersion


@router.get(
    "/{chunk_id}",
    response_model=SourceChunk,
    responses={404: {"description": "No active chunk with this id"}, 503: {}},
)
async def get_source(chunk_id: uuid.UUID, request: Request) -> SourceChunk:
    engine: AsyncEngine = request.app.state.engine
    stmt = (
        select(
            DocumentChunk.id,
            DocumentChunk.heading_path,
            DocumentChunk.start_line,
            DocumentChunk.end_line,
            DocumentChunk.text,
            ChunkSet.chunker_version,
            ChunkSet.embedding_model,
            Document.source_key,
            Document.title,
            Document.kind,
            Document.service_slug,
            Document.source_url,
            DocumentVersion.source_repository,
            DocumentVersion.source_revision,
            DocumentVersion.source_file_url,
            DocumentVersion.content_sha256,
            DocumentVersion.retrieved_on,
            DocumentVersion.license,
            DocumentVersion.attribution,
        )
        .join(ChunkSet, ChunkSet.id == DocumentChunk.chunk_set_id)
        .join(Document, Document.id == ChunkSet.document_id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .where(DocumentChunk.id == chunk_id, ChunkSet.is_active)
    )
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        log.warning("source lookup failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="database unavailable") from None
    if row is None:
        raise HTTPException(status_code=404, detail="source not found")

    return SourceChunk(
        chunk_id=row.id,
        heading_path=list(row.heading_path),
        start_line=row.start_line,
        end_line=row.end_line,
        text=row.text,
        chunker_version=row.chunker_version,
        embedding_model=row.embedding_model,
        document=SourceDocument(
            source_key=row.source_key,
            title=row.title,
            kind=row.kind,
            service=row.service_slug,
            source_url=row.source_url,
        ),
        version=SourceVersion(
            source_repository=row.source_repository,
            source_revision=row.source_revision,
            source_file_url=row.source_file_url,
            content_sha256=row.content_sha256,
            retrieved_on=row.retrieved_on,
            license=row.license,
            attribution=row.attribution,
        ),
    )
