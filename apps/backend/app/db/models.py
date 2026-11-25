"""Relational schema: reference services, source documents, and stored chunks.

Document identity is the logical source (`documents.source_key`). Each retrieved
revision of that source is a `document_versions` row. The content hash detects
identical content across revisions; it is not the document's identity.

Chunks are stored in `chunk_sets`: the complete output of one chunker version and
one embedding model for one document version. At most one set per document is
active; retrieval reads only active sets. See docs/adr/003-chunk-storage-and-embeddings.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Fixed in migration 0002 as vector(1024). Changing it needs a new migration and
# re-embedding every chunk (ADR 003). A test checks the column matches.
EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


class Service(Base):
    __tablename__ = "services"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    service_slug: Mapped[str | None] = mapped_column(
        ForeignKey("services.slug", ondelete="RESTRICT"), index=True
    )
    source_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "source_revision", name="uq_document_versions_revision"),
        UniqueConstraint("document_id", "content_sha256", name="uq_document_versions_content"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    source_repository: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String(64))
    source_file_url: Mapped[str] = mapped_column(Text)
    application_reference: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    retrieved_on: Mapped[date] = mapped_column(Date)
    license: Mapped[str] = mapped_column(String(64))
    attribution: Mapped[str] = mapped_column(Text)
    modified: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionRun(Base):
    """Audit record of one `python -m app.ingest` invocation."""

    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # running | succeeded | failed
    chunker_version: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_ingested: Mapped[int] = mapped_column(Integer, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, default=0)
    documents_failed: Mapped[int] = mapped_column(Integer, default=0)
    chunks_written: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChunkSet(Base):
    """All chunks of one document version for one (chunker, embedding model) pair."""

    __tablename__ = "chunk_sets"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunker_version",
            "embedding_model",
            name="uq_chunk_sets_version_config",
        ),
        Index(
            "uq_chunk_sets_one_active_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    chunker_version: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_set_id", "index", name="uq_document_chunks_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    chunk_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunk_sets.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from chunk_sets so a citation resolves to its version directly.
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(VECTOR(EMBEDDING_DIM))
    # Generated by PostgreSQL from `text` (migration 0003, ADR 004); never written.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, text)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
