# Handoff: Week 2 (2A–2E done, 2F next)

> For whoever picks this up next, person or Claude. Continue 2F–2H from this
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
- **2C, ingestion pipeline and schema:** pushed as `ef73991`. CI run
  45419915660 passed every job.
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

### 2D Retrieval and source API

Pushed as `5a31e3f`. CI run 45535981832 passed every job.

- `GET /api/sources/{chunk_id}` (`app/api/sources.py`):
  - Returns chunk text, heading_path, line range, chunker and embedding
    versions, the document, and the version provenance.
  - 404 `{"detail": "source not found"}` for unknown or inactive chunks; 422
    for a non-UUID; 503 `{"detail": "database unavailable"}` with no
    connection details.
- Settings: `RETRIEVAL_TOP_K` (5) and `RETRIEVAL_MAX_DISTANCE` (0.8).
- The `seeded` and `ingested` fixtures live in `tests/conftest.py`.

### Hybrid retrieval (ADR 004)

Pushed as `19aeb90`. CI run 45537073739 passed every job.

Under mock vectors, natural-language questions retrieved nothing, so the user
chose (2026-11-25) to add PostgreSQL full-text search now, as ADR 001 had
planned.

- **Migration `0003_chunk_fulltext.py`:** adds `document_chunks.search_vector`,
  `GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED`, with
  a GIN index. Ingestion is unchanged, and existing rows are backfilled.
- **`app/rag/retrieval.py`:**
  - `vector_search(...)`: the 2D vector channel. `score` is the cosine
    distance.
  - `lexical_search(engine, query, *, top_k, services=None)`:
    - Uses `plainto_tsquery`, so user input can't inject operators.
    - Rewrites `&` to `|`, so a chunk matches if it has any term.
    - Ranks with `ts_rank(..., 1)`; ties break by chunk ID.
  - `fuse(channels, *, top_k)`: RRF with k = 60. Keeps `vector_distance` and
    `lexical_score` on each result.
  - `retrieve(engine, provider, query, *, top_k, max_distance, candidates,
    services=None)`: both channels, `candidates` each, fused to `top_k`.
- **Setting:** `RETRIEVAL_CANDIDATES`, default 20, range 1–200.
- **Tests:**
  - `tests/unit/test_fusion.py`.
  - In `tests/integration/test_retrieval.py`:
    - The generated column is filled for every chunk.
    - Stemmed any-term match.
    - Operators and stop words in input are harmless.
    - Service and active-set filters.
    - A chunk found by both channels ranks first.
    - Lexical fallback when vectors find nothing.
    - Hybrid finds nothing when no term matches.
    - The `candidates` vs `top_k` check.
- **Local verification:** 87 backend tests pass; ruff and mypy strict pass.
  Migration 0003 downgrades and upgrades cleanly. In Compose it applied on top
  of existing data.
- **Ad-hoc dev check, not a quality claim:** a relevant document was in the
  hybrid top 5 for 7 of the 9 answerable dev cases. Both misses expect
  `architecture.md`, whose large diagram chunk ranks low.
- **Important for 2E:** both `insufficient_evidence` dev cases still return
  candidates, since in-domain words match. Retrieval being non-empty is
  **not** enough to answer. 2E must abstain when the cited chunks don't
  support a claim, and mock behavior must not fake support.

### 2E Generation, citations, and abstention (ADR 005): done, not committed yet

- **`app/providers/llm.py`:**
  - `Evidence(label, title, heading_path, text)`.
  - `LLMProvider.draft_answer(question, evidence) -> str`, returning a JSON
    draft.
  - `MockLLMProvider` (`mock-extractive-v1`):
    - Quotes up to 3 prose sentences or list items verbatim, skipping code,
      headings, and tables.
    - A sentence needs at least 2 of the question's content terms and at
      least half of them.
    - Abstains otherwise.
  - `terms()` and `sentences()` helpers.
- **`app/rag/citations.py`:** `ground(raw, labels)`.
  - Malformed or invalid draft → abstain (`INVALID_DRAFT`).
  - A drafted abstention is honored.
  - Unknown labels are removed. Claims with no valid citation are dropped,
    with a limitation reporting the count.
  - Nothing left → abstain (`NO_GROUNDED_CLAIMS`).
- **`app/rag/answer.py`:** `answer_question(...)`.
  - Retrieves with hybrid search and labels the passages S1…Sn.
  - If retrieval is empty, abstains **without calling the LLM**.
  - Otherwise grounds the draft and resolves citations.
- **`POST /api/ask` (`app/api/ask.py`):**
  - Request: `{question (1–1000 chars), services?}`; extra fields are
    rejected.
  - Response: `abstained`, `summary`, `claims[{text,
    citations[{chunk_id, label, source_key, title, heading_path, start_line,
    end_line}]}]`, `limitations`, `retrieved[...]`, `llm_model`,
    `embedding_model`.
  - Errors: 422 schema; 400 blank question or unknown service; 503 index not
    ready or database unavailable; 502 provider failure. No details leak, and
    the question is never logged.
- **Retrieval errors:** `InvalidQuery` (client) and `IndexMismatch` (index)
  now subclass `RetrievalError`.
- **Providers:** created in `create_app` and stored as
  `app.state.embedding_provider` and `app.state.llm_provider`. Tests swap
  them there.
- **Tests:**
  - `tests/unit/test_answer_drafting.py`: terms, sentences, the mock quoting
    verbatim, abstention, and grounding rules.
  - `tests/integration/test_ask.py`:
    - Every claim appears in its cited source, and its citation resolves via
      `/api/sources`.
    - Insufficient-evidence questions abstain.
    - Empty retrieval never calls the LLM.
    - Fabricated citations are removed.
    - A malformed draft abstains.
    - 502, 503, 400, and 422 responses.
    - Service filter.
  - A database-down 503 test in `test_health_unavailable.py`.
- **Local verification:** 109 backend tests pass; ruff and mypy strict pass.
  In Compose, `/api/ask` answers the quote question with a quoted, cited
  sentence, and abstains on "What caused the checkout failures…".
- **Ad-hoc dev run, not a quality claim:**
  - Both `insufficient_evidence` cases abstained.
  - 5 of the 9 answerable cases got quoted answers.
  - The other 4 abstained (deploy ×2, kafka, frontend), because no single
    sentence covered enough of the question.
  - The architecture cases cite the shipping, quote, and checkout docs, not
    `architecture.md`.

## 3. Remaining phases

Dependencies: 2F, and 2G any time. 2H comes last.

### 2F Ask and citation UI

- An Ask page and a citation component. Clicking a citation opens the source
  passage. Sanitize Markdown and disallow raw HTML.
- Add `/api` to the proxy in `vite.config.ts`.
- Component tests cover: answer, abstention, errors, opening a citation.
- Present claims with their citation labels as the evidence. Show the summary
  as orientation only, and always show the limitations (ADR 005).
- Clicking a citation calls `GET /api/sources/{chunk_id}` and shows the passage,
  heading path, line range, and the `source_url` / `source_file_url` links.

### 2G Bedrock adapter (code only)

- `app/providers/bedrock.py`:
  - Embeddings use Titan V2 through `invoke_model`, with `dimensions=1024` and
    `normalize=true`.
  - Generation uses Converse, with a system prompt stating the ADR 005 rules
    and JSON output. Retry at most once on a malformed draft, then abstain.
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
