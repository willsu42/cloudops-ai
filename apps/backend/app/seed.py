"""Seed reference services and corpus document metadata from datasets/corpus/manifest.json.

Run with: python -m app.seed

The seed is transactional and repeatable: identifiers are deterministic
(UUIDv5 of stable keys), and rerunning it with unchanged inputs leaves every row
exactly as it was. Only metadata is stored; document text is not ingested yet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.logging import configure_logging
from app.db import create_engine
from app.db.models import Document, DocumentVersion, Service

log = logging.getLogger(__name__)

# Fixed namespace so identifiers are stable across machines and reseeds.
ID_NAMESPACE = uuid.UUID("6f1c2b0e-3d7a-5b8e-9c41-2a7d0e5f9b13")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestService(_Strict):
    slug: str
    name: str
    language: str | None
    role: str


class ManifestDocument(_Strict):
    source_key: str
    title: str
    kind: str
    service: str | None
    local_path: str
    source_url: str
    source_file_url: str
    source_revision: str
    modified: bool
    # Overrides the manifest-level retrieval date for documents fetched later.
    retrieved_on: date | None = None


class DocumentationSource(_Strict):
    repository: str
    commit: str
    commit_date: str
    license: str
    license_url: str
    attribution: str


class ApplicationReference(_Strict):
    repository: str
    main_commit_at_retrieval: str
    latest_release_at_retrieval: str
    license: str
    note: str


class Manifest(_Strict):
    schema_version: int
    corpus: str
    retrieved_on: date
    documentation_source: DocumentationSource
    application_reference: ApplicationReference
    services: list[ManifestService]
    documents: list[ManifestDocument]


def document_id(source_key: str) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"document:{source_key}")


def version_id(source_key: str, revision: str) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"document-version:{source_key}@{revision}")


def load_manifest(datasets_dir: Path) -> Manifest:
    raw = json.loads((datasets_dir / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    manifest = Manifest.model_validate(raw)
    slugs = {s.slug for s in manifest.services}
    for doc in manifest.documents:
        if doc.service is not None and doc.service not in slugs:
            raise ValueError(f"{doc.source_key}: unknown service {doc.service!r}")
    return manifest


def build_rows(manifest: Manifest, repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    src = manifest.documentation_source
    app_ref = manifest.application_reference
    app_ref_text = (
        f"{app_ref.repository} main@{app_ref.main_commit_at_retrieval[:7]}; "
        f"latest release {app_ref.latest_release_at_retrieval}"
    )
    services = [s.model_dump() for s in manifest.services]
    documents: list[dict[str, Any]] = []
    versions: list[dict[str, Any]] = []
    for doc in manifest.documents:
        content = (repo_root / doc.local_path).read_bytes()
        documents.append(
            {
                "id": document_id(doc.source_key),
                "source_key": doc.source_key,
                "title": doc.title,
                "kind": doc.kind,
                "service_slug": doc.service,
                "source_url": doc.source_url,
            }
        )
        versions.append(
            {
                "id": version_id(doc.source_key, doc.source_revision),
                "document_id": document_id(doc.source_key),
                "source_repository": src.repository,
                "source_revision": doc.source_revision,
                "source_file_url": doc.source_file_url,
                "application_reference": app_ref_text,
                "local_path": doc.local_path,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "byte_size": len(content),
                "retrieved_on": doc.retrieved_on or manifest.retrieved_on,
                "license": src.license,
                "attribution": src.attribution,
                "modified": doc.modified,
            }
        )
    return {"services": services, "documents": documents, "versions": versions}


async def seed(engine: AsyncEngine, datasets_dir: Path) -> dict[str, int]:
    manifest = load_manifest(datasets_dir)
    rows = build_rows(manifest, datasets_dir.parent)

    async with engine.begin() as conn:
        stmt = insert(Service).values(rows["services"])
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[Service.slug],
                set_={c: stmt.excluded[c] for c in ("name", "language", "role")},
            )
        )
        stmt = insert(Document).values(rows["documents"])
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[Document.source_key],
                set_={c: stmt.excluded[c] for c in ("title", "kind", "service_slug", "source_url")},
            )
        )
        # Identical content for a document is stored once: a later revision with
        # the same hash keeps the first-seen version row.
        stmt = insert(DocumentVersion).values(rows["versions"])
        await conn.execute(stmt.on_conflict_do_nothing(constraint="uq_document_versions_content"))

    counts = {k: len(v) for k, v in rows.items()}
    log.info("seed complete", extra=counts)
    return counts


async def _main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings, application_name="cloudops-seed")
    try:
        await seed(engine, settings.datasets_dir)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
