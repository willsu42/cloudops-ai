"""Create ingestion_runs, chunk_sets, and document_chunks with vector(1024).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24

The embedding dimension is a literal here on purpose (ADR 003): the column type
is the single source of truth, and a vector of any other length is rejected at
insert. Changing it requires a new migration and re-embedding every chunk.
No ANN index yet; retrieval uses exact cosine search over a small corpus.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("chunker_version", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("documents_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_ingestion_runs_status"
        ),
    )

    op.create_table(
        "chunk_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunker_version", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "ingestion_run_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        _created_at(),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "document_version_id",
            "chunker_version",
            "embedding_model",
            name="uq_chunk_sets_version_config",
        ),
    )
    op.create_index("ix_chunk_sets_document_id", "chunk_sets", ["document_id"])
    op.create_index("ix_chunk_sets_document_version_id", "chunk_sets", ["document_version_id"])
    # At most one active set per document: activation swaps sets atomically.
    op.create_index(
        "uq_chunk_sets_one_active_per_document",
        "chunk_sets",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "chunk_set_id",
            sa.Uuid(),
            sa.ForeignKey("chunk_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=False),
        _created_at(),
        sa.UniqueConstraint("chunk_set_id", "index", name="uq_document_chunks_position"),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line", name="ck_document_chunks_lines"
        ),
    )
    op.create_index("ix_document_chunks_chunk_set_id", "document_chunks", ["chunk_set_id"])
    op.create_index(
        "ix_document_chunks_document_version_id", "document_chunks", ["document_version_id"]
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("chunk_sets")
    op.drop_table("ingestion_runs")
