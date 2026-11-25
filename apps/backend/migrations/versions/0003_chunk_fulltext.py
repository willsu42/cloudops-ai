"""Add full-text search to document_chunks for lexical and hybrid retrieval.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25

`search_vector` is a stored generated column, so PostgreSQL keeps it in sync
with `text` and ingestion needs no change. The text search configuration is
named explicitly ('english'), which makes the expression immutable and the
results independent of the server's default_text_search_config. See ADR 004.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_chunks ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_search_vector ON document_chunks USING gin (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_document_chunks_search_vector")
    op.execute("ALTER TABLE document_chunks DROP COLUMN search_vector")
