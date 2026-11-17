"""Enable pgvector; create services, documents, and document_versions.

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No vector columns yet; chunk embeddings arrive with ingestion (Week 2),
    # and their dimension will be fixed in that migration.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "services",
        sa.Column("slug", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("language", sa.String(64), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_key", sa.String(128), nullable=False, unique=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "service_slug",
            sa.String(64),
            sa.ForeignKey("services.slug", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_documents_service_slug", "documents", ["service_slug"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_repository", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("source_file_url", sa.Text(), nullable=False),
        sa.Column("application_reference", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("retrieved_on", sa.Date(), nullable=False),
        sa.Column("license", sa.String(64), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("modified", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "source_revision", name="uq_document_versions_revision"),
        sa.UniqueConstraint("document_id", "content_sha256", name="uq_document_versions_content"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_content_sha256", "document_versions", ["content_sha256"])


def downgrade() -> None:
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("services")
    # The vector extension is left installed; other objects may depend on it.
