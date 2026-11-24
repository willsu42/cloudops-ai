# Handoff: Week 2 (2A–2C done, 2D next)

> For whoever picks this up next, person or Claude. Continue 2D–2H from this
> file, and delete it once Week 2 is complete.
> The original phase design is in
> `/Users/willsu/.claude/plans/based-on-this-plan-wiggly-simon.md`. That file
> now holds the 2C plan.

## 1. Direction and constraints (carried over from Week 1)

- The reference system is the OpenTelemetry Demo, scoped to the checkout flow.
  Week 2's goal is document ingestion plus basic RAG, with answers that carry
  traceable citations.
- **AWS is not enabled yet.** All of Week 2 runs on mock providers. The Bedrock
  adapter is written and tested with stubs only. The smoke test against real
  AWS comes after the account is enabled.
- Mock embeddings only show that the pipeline runs end to end. They are **not**
  evidence of retrieval quality.
- Commit, push, deploy, call paid APIs, or add a LICENSE only when the user
  explicitly asks. Ask before every commit.
- One commit per phase. After each push, confirm CI is green.

## 2. Done

- **2A and 2B:** pushed as `2ab5ec8`. CI run 45206813408 passed every job.
  - The corpus has 12 Markdown documents and 11 dev cases.
  - Parsing and chunking live in `app/rag/parsing.py` and
    `app/rag/chunking.py`. The chunker version is `md-heading-v1`.
  - **Correction:** the earlier "105 chunks" was an outdated figure. Under the
    current rules, the 12 documents produce **104 chunks**.
- **2C, ingestion pipeline and schema:** done, **not committed yet**.
  - The user confirmed two decisions on 2026-11-24:
    - The vector dimension is **1024**.
    - PDF is **rejected explicitly** and not parsed.
  - Reasons, alternatives, limitations, and future extensions for these
    decisions are recorded in
    **`docs/adr/003-chunk-storage-and-embeddings.md`**.

### What 2C contains

- **Migration `0002_chunks.py`:**
  - `ingestion_runs`: audit records with status, counts, and error.
  - `chunk_sets`: one row per (document version × chunker version × embedding
    model), with an `is_active` column. A partial unique index allows only one
    active set per document.
  - `document_chunks`: holds `embedding vector(1024)` (dimension fixed in the
    migration), `heading_path text[]`, the line range, and a denormalized
    `document_version_id`.
- **`app/db/models.py`:** three new ORM classes and `EMBEDDING_DIM = 1024`.
- **`app/providers/embeddings.py`:**
  - `EmbeddingProvider` protocol, with an async `embed`.
  - `MockEmbeddingProvider` (`mock-hash-v1`): vectors from SHAKE-256,
    L2-normalized, with no semantic meaning.
  - `validate_embeddings` and `get_embedding_provider`.
- **`app/ingest.py`:** run with `python -m app.ingest` or `make ingest`.
  - Only `.md` is accepted. Any other format returns
    `unsupported format '.pdf': only Markdown (.md) is ingested`.
  - Each file is matched to its seeded version by content hash. On a mismatch
    nothing is written, so no chunk is ever built from content that differs
    from the recorded version.
  - Chunk set IDs are UUIDv5 values:
    - An already active set is skipped.
    - An existing but inactive set is re-activated without re-embedding.
    - A new set is written and switched to active in a single transaction.
  - Each document uses its own transaction, so one failure doesn't affect the
    others. If any document fails, the run is marked `failed` and the CLI
    exits 1.
  - Error messages are truncated. Database errors record only the exception
    type, never parameters.
- **Dependencies:** added `pgvector==0.4.2`, which brings in numpy. The mypy
  config sets `ignore_missing_imports` for `pgvector.*`.
  - **Note:** do not call `register_vector` on asyncpg. It conflicts with the
    text binding of SQLAlchemy's `VECTOR` type; trying it produced write
    errors.
- **Makefile:** new `ingest` target.
- **Docs:** added ADR 003. Updated the README, the ER diagram in
  architecture.md, and the cross-reference in ADR 001.

### Verification (local)

| Check | Result |
| --- | --- |
| Backend tests (real pgvector test DB) | 57 passed (new: `test_embeddings.py`, `test_ingest.py`) |
| ruff format, ruff check, mypy strict, `uv lock --check` | All pass |
| Compose: `make up`, `make seed`, then `make ingest` twice | First run: 12 documents ingested, 104 chunks. Second run: all 12 skipped. `format_type` = `vector(1024)`, 12 active sets |
| Migration 0002 downgrade, then upgrade | Works |

GitHub CI has not run yet; it runs after a push.

## 3. Remaining phases

Dependencies: 2D → 2E → 2F. 2G can happen any time after 2E. 2H comes last.

### 2D Retrieval and source API

- `app/rag/retrieval.py`:
  - Exact cosine search with `<=>`, **over chunks in active `chunk_sets`
    only**.
  - Optional service filter: JOIN on `documents.service_slug`.
  - Apply filters before ranking, then take the top k.
  - Embed the query with the same provider. Check that its `model_id` matches
    the active sets' `embedding_model`, and raise an error if not, because
    vectors from different models can't be compared.
  - Put the empty-retrieval threshold (a distance cap) in settings.
- `GET /api/sources/{chunk_id}`:
  - Returns text, heading_path, the line range, source_url, source_file_url,
    and version details (source_revision, retrieved_on).
  - Returns 404 when the chunk doesn't exist or isn't in an active set.
- Tests:
  - With the mock provider, querying with a chunk's own text should rank that
    chunk first, since the vectors are identical.
  - Filters take effect.
  - Inactive chunks are never returned.
  - The source API's 404.

### 2E Generation, citations, and abstention

- `app/providers/llm.py`: an `LLMProvider` protocol and a mock with fixed
  output.
- Answer schema: summary, claims (each with a citation chunk ID), limitations,
  abstained.
- `app/rag/citations.py`: every citation must appear in this request's
  retrieval results. Invalid ones are removed or flagged. When retrieval comes
  back empty, abstain without calling the LLM.
- `POST /api/ask`: synchronous, with a maximum question length.

### 2F Ask and citation UI

- An Ask page and a citation component. Clicking a citation opens the source
  passage. Sanitize Markdown and disallow raw HTML.
- Add `/api` to the proxy in `vite.config.ts`.
- Component tests cover: answer, abstention, errors, opening a citation.

### 2G Bedrock adapter (code only)

- `app/providers/bedrock.py`:
  - Embeddings use Titan V2 through `invoke_model`, with `dimensions=1024` and
    `normalize=true`.
  - Generation uses Converse.
  - Tests use botocore Stubber.
  - Needs a new boto3 dependency.
- `PROVIDER_MODE=aws` **stays refused** until a real smoke test passes.
- Add `docs/rag.md` covering the model, dimension, cosine distance, and the
  reindex procedure, marked "pending AWS verification". It can refer to
  ADR 003.

### 2H Wrap-up

- Run the full flow with mocks: 12 documents and at least 10 questions.
- The PDF rejection path already has a test from 2C ingest
  (`test_pdf_is_rejected_with_an_explicit_limitation`). Once upload or ask
  interfaces exist, confirm that the limitation reaches the user.
- Update the Week 2 checkboxes in the README and PLAN §7. Mark items that
  depend on a real provider as "pending AWS".

## 4. Open decisions

- **AWS account and region:** not enabled yet. Enable before Week 3 at the
  latest, and request Bedrock model access.
- The vector dimension (1024) and PDF handling (reject only) are **decided**;
  see ADR 003.

## 5. Environment tips

- Node 24 is in `/usr/local/bin`, but Homebrew's Node 25 comes first on PATH.
  Run `export PATH=/usr/local/bin:$PATH` before frontend commands.
- pnpm isn't installed globally. Use `make PNPM="npx --yes pnpm@12.17.0" ...`.
- Run `unset VIRTUAL_ENV` before uv commands to avoid Anaconda interference.
- Docker Desktop may not be running. Start it with `open -a Docker`.
- Start the test database with `make test-db`, and stop it with
  `make test-db-down`.
