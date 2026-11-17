"""Relational schema for Week 1: reference services and source-document metadata.

Document identity is the logical source (`documents.source_key`). Each retrieved
revision of that source is a `document_versions` row. The content hash detects
identical content across revisions; it is not the document's identity.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
