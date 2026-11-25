"""Ingestion against the dedicated PostgreSQL/pgvector test database."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import EMBEDDING_DIM
from app.ingest import IngestReport, ingest
from app.providers.embeddings import EmbeddingError, MockEmbeddingProvider
from app.rag.chunking import chunk_markdown
from app.seed import load_manifest
from tests.conftest import DATASETS_DIR

pytestmark = pytest.mark.db

MANIFEST = load_manifest(DATASETS_DIR)
EXPECTED_CHUNKS = sum(
    len(chunk_markdown((DATASETS_DIR.parent / d.local_path).read_text(encoding="utf-8")))
    for d in MANIFEST.documents
)


class CountingProvider(MockEmbeddingProvider):
    def __init__(self, model_id: str = "mock-hash-v1") -> None:
        super().__init__(model_id)
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return await super().embed(texts)


class BrokenProvider(MockEmbeddingProvider):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("upstream timeout")


class ShortVectorProvider(MockEmbeddingProvider):
    """Claims 1024 dimensions but returns 1023."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [v[:-1] for v in await super().embed(texts)]


async def _rows(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


async def _active_chunks(engine: AsyncEngine) -> list[dict[str, Any]]:
    return await _rows(
        engine,
        "SELECT c.id, c.text, s.embedding_model FROM document_chunks c "
        "JOIN chunk_sets s ON s.id = c.chunk_set_id WHERE s.is_active ORDER BY c.id",
    )


async def _active_models(engine: AsyncEngine) -> set[str]:
    rows = await _rows(engine, "SELECT embedding_model FROM chunk_sets WHERE is_active")
    return {r["embedding_model"] for r in rows}


def _counts(report: IngestReport) -> dict[str, int]:
    return {o: report.count(o) for o in ("ingested", "reactivated", "skipped", "failed")}


async def test_embedding_column_is_fixed_at_1024(db_engine: AsyncEngine) -> None:
    rows = await _rows(
        db_engine,
        "SELECT format_type(atttypid, atttypmod) AS type FROM pg_attribute "
        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'",
    )
    assert rows == [{"type": f"vector({EMBEDDING_DIM})"}]
    assert EMBEDDING_DIM == 1024


async def test_ingest_stores_every_chunk_and_is_repeatable(seeded: AsyncEngine) -> None:
    first = await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider())
    assert first.status == "succeeded"
    assert _counts(first)["ingested"] == len(MANIFEST.documents)
    before = await _active_chunks(seeded)
    assert len(before) == EXPECTED_CHUNKS

    provider = CountingProvider()
    second = await ingest(seeded, DATASETS_DIR, provider)
    assert _counts(second) == {"ingested": 0, "reactivated": 0, "skipped": 12, "failed": 0}
    assert provider.calls == 0
    assert await _active_chunks(seeded) == before

    runs = await _rows(
        seeded,
        "SELECT status, documents_ingested, documents_skipped, chunks_written "
        "FROM ingestion_runs ORDER BY started_at",
    )
    assert runs == [
        {"status": "succeeded", "documents_ingested": 12, "documents_skipped": 0,
         "chunks_written": EXPECTED_CHUNKS},
        {"status": "succeeded", "documents_ingested": 0, "documents_skipped": 12,
         "chunks_written": 0},
    ]  # fmt: skip


async def test_chunk_ids_are_deterministic_across_databases(seeded: AsyncEngine) -> None:
    await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider())
    before = await _active_chunks(seeded)
    async with seeded.begin() as conn:
        await conn.execute(text("TRUNCATE chunk_sets, ingestion_runs CASCADE"))
    await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider())
    assert await _active_chunks(seeded) == before


async def test_stored_chunks_point_back_to_their_lines(seeded: AsyncEngine) -> None:
    await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider())
    rows = await _rows(
        seeded,
        "SELECT c.text, c.start_line, c.end_line, v.local_path FROM document_chunks c "
        "JOIN document_versions v ON v.id = c.document_version_id",
    )
    for row in rows:
        lines = (DATASETS_DIR.parent / row["local_path"]).read_text(encoding="utf-8").split("\n")
        span = "\n".join(lines[row["start_line"] - 1 : row["end_line"]])
        first_line = row["text"].split("\n", 1)[0].strip()
        assert first_line in span, row["local_path"]


async def test_new_model_swaps_active_sets_and_old_one_can_return(seeded: AsyncEngine) -> None:
    await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider("mock-hash-v1"))
    v1 = await _active_chunks(seeded)

    v2_report = await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider("mock-hash-v2"))
    assert _counts(v2_report)["ingested"] == 12
    assert await _active_models(seeded) == {"mock-hash-v2"}
    assert len(await _active_chunks(seeded)) == EXPECTED_CHUNKS

    provider = CountingProvider("mock-hash-v1")
    back = await ingest(seeded, DATASETS_DIR, provider)
    assert _counts(back)["reactivated"] == 12
    assert provider.calls == 0  # re-activation does not re-embed
    assert await _active_chunks(seeded) == v1


@pytest.mark.parametrize(
    ("provider", "error"),
    [
        (BrokenProvider("mock-hash-v2"), "embedding provider failed: RuntimeError"),
        (ShortVectorProvider("mock-hash-v2"), "has 1023 dimensions, expected 1024"),
    ],
)
async def test_failed_embedding_keeps_previous_active_set(
    seeded: AsyncEngine, provider: MockEmbeddingProvider, error: str
) -> None:
    await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider("mock-hash-v1"))
    before = await _active_chunks(seeded)

    report = await ingest(seeded, DATASETS_DIR, provider)

    assert report.status == "failed"
    assert _counts(report)["failed"] == 12
    assert all(error in (r.error or "") for r in report.results)
    assert await _active_chunks(seeded) == before
    leftovers = await _rows(
        seeded, "SELECT count(*) AS n FROM chunk_sets WHERE embedding_model = 'mock-hash-v2'"
    )
    assert leftovers == [{"n": 0}]
    run = await _rows(
        seeded, "SELECT status, documents_failed, error FROM ingestion_runs ORDER BY started_at"
    )
    assert run[-1]["status"] == "failed"
    assert run[-1]["documents_failed"] == 12
    assert error in run[-1]["error"]


async def test_wrong_dimension_provider_is_refused_before_writing(seeded: AsyncEngine) -> None:
    with pytest.raises(EmbeddingError, match="512-dimensional"):
        await ingest(seeded, DATASETS_DIR, MockEmbeddingProvider(dim=512))
    assert await _rows(seeded, "SELECT id FROM ingestion_runs") == []


def _copy_datasets(tmp_path: Path) -> Path:
    target = tmp_path / "datasets"
    shutil.copytree(DATASETS_DIR / "corpus", target / "corpus")
    return target


async def test_changed_file_is_not_ingested(seeded: AsyncEngine, tmp_path: Path) -> None:
    datasets = _copy_datasets(tmp_path)
    changed = MANIFEST.documents[0]
    path = tmp_path / changed.local_path
    path.write_text(path.read_text(encoding="utf-8") + "\nInvented line.\n", encoding="utf-8")

    report = await ingest(seeded, datasets, MockEmbeddingProvider())

    failed = [r for r in report.results if r.outcome == "failed"]
    assert [r.source_key for r in failed] == [changed.source_key]
    assert "no seeded document version matches" in (failed[0].error or "")
    assert _counts(report)["ingested"] == 11
    sets = await _rows(seeded, "SELECT count(*) AS n FROM chunk_sets")
    assert sets == [{"n": 11}]


async def test_pdf_is_rejected_with_an_explicit_limitation(
    seeded: AsyncEngine, tmp_path: Path
) -> None:
    datasets = _copy_datasets(tmp_path)
    pdf = datasets / "corpus" / "otel-demo" / "runbook.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%fixture, not a real document\n")
    manifest_path = datasets / "corpus" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"].append(
        {
            **manifest["documents"][0],
            "source_key": "fixture-runbook-pdf",
            "local_path": "datasets/corpus/otel-demo/runbook.pdf",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = await ingest(seeded, datasets, MockEmbeddingProvider())

    pdf_result = next(r for r in report.results if r.source_key == "fixture-runbook-pdf")
    assert pdf_result.outcome == "failed"
    assert pdf_result.error == "unsupported format '.pdf': only Markdown (.md) is ingested"
    assert report.status == "failed"
    assert _counts(report)["ingested"] == 12  # the Markdown documents are unaffected
