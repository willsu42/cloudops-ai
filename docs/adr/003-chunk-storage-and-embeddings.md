# ADR 003: Chunk storage, embedding dimension, and accepted formats

- **Status:** Accepted (implemented in Week 2, phase 2C, with the mock embedding
  provider; the real-model parts are pending AWS access)
- **Date:** 2026-11-24

## Context

Phase 2B produces heading-aware chunks with line ranges
(`app/rag/chunking.py`, `CHUNKER_VERSION = "md-heading-v1"`). Phase 2C stores
them with embeddings so that phase 2D can retrieve them and phase 2E can cite
them.

Several choices here are hard to reverse:

- A pgvector column has a fixed dimension. Changing it later needs a migration,
  and every stored chunk has to be re-embedded.
- Re-ingesting a document (a new revision, a new chunker, a new embedding
  model) must never leave retrieval with half a document, or with nothing, if
  the run fails partway.
- Citations point to stored chunks, so the format of a source decides what a
  citation can point to (a line range, a page, ...).

Constraints at the time of the decision:

- AWS is not enabled yet, so no real embedding model can be called.
- The corpus is 12 OpenTelemetry Demo Markdown pages, 104 chunks, about 10k
  tokens. It contains no PDFs.
- The planned cloud model is Amazon Titan Text Embeddings V2 on Bedrock
  (ADR 001).

## Decision

### 1. Embedding dimension 1024, fixed in the migration

`document_chunks.embedding` is `vector(1024)`. Migration `0002` writes the number
as a literal. `app/db/models.py` defines `EMBEDDING_DIM = 1024`, and a test checks
that the database column matches it.

Ingestion refuses a provider whose `dim` differs, before writing anything. It
also validates every returned vector's length and values.

Reasons:

- 1024 is Titan Text Embeddings V2's default and largest output size. Titan V2
  can also produce 256 or 512.
- Cohere Embed v3 on Bedrock also produces 1024 dimensions. Switching to it
  would need re-embedding, but no schema change.
- At this corpus size, storage does not matter: about 4 KB per vector,
  roughly 0.4 MB in total.
- With the dimension in the column type, a wrong-sized vector fails at insert.
  A dimension read from configuration could drift from the table without
  anything failing until write time.

### 2. Chunk sets with an atomic active switch

There are three tables:

- `ingestion_runs`: one audit row per run, with status, counts, and errors.
- `chunk_sets`: one row per (document version, chunker version, embedding
  model).
- `document_chunks`: the chunks of a set.

How activation works:

- A partial unique index allows **one active set per document**. Retrieval
  will read only active sets.
- A new set is written and activated in one transaction: insert the set and its
  chunks, deactivate the old set, activate the new one.
- If embedding or writing fails, the transaction rolls back and the previous
  set stays active.
- Set and chunk IDs are UUIDv5 values from stable keys. Re-running ingestion
  skips sets that already exist. An existing but inactive set, for example
  after switching back to an earlier model, is re-activated without being
  re-embedded.
- Only Markdown whose SHA-256 matches a seeded `document_versions` row is
  ingested. A changed local file fails, so stored text always comes from the
  recorded version.

### 3. One transaction per document

Each document is ingested in its own transaction, so one failure does not block
the others. The run is marked `failed` if any document failed, and the CLI then
exits 1.

### 4. Deterministic mock embeddings until AWS is available

`MockEmbeddingProvider` (`mock-hash-v1`) expands a SHAKE-256 hash of the text
into 1024 values and L2-normalizes them. It exists to test the pipeline. **It
has no semantic meaning, so retrieval quality measured with it is not
evidence of anything.**

Providers implement the async `EmbeddingProvider` protocol
(`app/providers/embeddings.py`).

### 5. Markdown only; other formats are rejected explicitly

Only `.md` files are ingested. Any other format, PDF included, fails with
`unsupported format '.pdf': only Markdown (.md) is ingested`. The failure is
recorded on the run, and no partial content is stored.

Reasons:

- Citations are "file + line range". PDFs have no stable line numbers.
- PDF text extraction is unreliable: tables, multi-column layouts, headers and
  footers, and scanned pages all cause problems.
- The corpus has no PDFs, so a parser could only be tested against invented
  fixtures.
- Parsing untrusted PDFs adds attack surface, and there is no upload API yet
  (Week 5).

### 6. No approximate-nearest-neighbour index yet

Phase 2D will use exact cosine distance (`<=>`). At 104 chunks a sequential
scan is fast and has perfect recall, so there is no recall–speed trade-off to
tune yet.

## Alternatives considered

- **Dimension 256 or 512.** Smaller vectors, and Titan V2 supports both, with
  a small accuracy loss. Rejected because there is no storage or speed pressure
  at this size.
- **Dimension from configuration.** Rejected: the column type must match the
  configured value, and nothing would enforce that.
- **One table per model or dimension.** This allows several dimensions to live
  side by side, but complicates retrieval now. It is kept as a future migration
  path (below).
- **An `is_active` flag on every chunk row.** Switching would update many rows,
  and nothing would stop two versions of a document from both being active.
  Rejected in favour of one flag per set plus a unique index.
- **Delete and re-insert on re-ingest.** Simpler, but a failure partway through
  leaves the document with no chunks. Rejected.
- **Blue/green copies of the whole chunk table.** This makes the whole corpus
  atomic, but swapping per document is enough here.
- **One transaction for the whole corpus.** Everything-or-nothing, but one bad
  document blocks all the others. Rejected.
- **A local embedding model (e.g. sentence-transformers).** It would give real
  semantics before AWS is available, but it is a heavy dependency, its results
  would not match the Bedrock model's, and a model download in CI would make
  builds slower and less stable. Deferred; possible as an offline comparison.
- **PDF parsing with pypdf or pdfminer.** Rejected for now (see decision 5).
- **An HNSW or IVFFlat index now.** It adds approximation and tuning
  parameters with no benefit at 104 chunks. Deferred.

## Consequences

- Changing the embedding dimension means a new migration and re-embedding the
  corpus. At about 10k tokens the model cost is negligible. The real costs are
  re-running evaluation and the operational steps.
- Switching models means re-embedding, even when the dimension stays the same.
  Vectors from different models cannot be compared.
- Titan V2's actual 1024-dimensional output on Bedrock is **not yet verified**.
  It is checked in the post-AWS smoke test. `PROVIDER_MODE=aws` stays refused
  until then.
- Inactive chunk sets are kept, which makes switching back instant, but they
  accumulate. There is no cleanup command yet.
- A run can partly succeed. Its `ingestion_runs` row records which documents
  failed and why. Error text is short; database errors are recorded only by
  exception type, so parameters and connection details are never stored.
- Until a real provider is enabled, any retrieval or answer-quality number is
  meaningless.
- The system cannot use PDF runbooks or reports. Asking it to ingest one gives
  an explicit limitation, not a silent skip.
- The pipeline adds the `pgvector` Python package, which brings in numpy, for
  the SQLAlchemy `VECTOR` type.

## Future extensions

- **Real embeddings (2G and after AWS):**
  - Add a `BedrockEmbeddingProvider` for Titan V2 through `invoke_model`, with
    `dimensions=1024` and `normalize=true`.
  - Test it with botocore Stubber, then run a cost-capped smoke test.
  - Re-ingest. The new `embedding_model` makes new sets, which replace the mock
    sets through the same atomic switch.
- **A different dimension:**
  - Add a migration with a new column or table, for example
    `document_chunks_v2` with `vector(N)`.
  - Ingest new sets alongside the old ones, switch retrieval over, then drop the
    old column.
- **Cleanup:** add a retention command that deletes inactive sets older than N
  days, or all but the last K per document.
- **PDF and other formats:**
  - Add `app/rag/pdf.py` with its own chunker version, such as `pdf-page-v1`.
  - Add nullable page columns (`start_page`, `end_page`) beside the line range,
    and let citations show pages.
  - Extend `SUPPORTED_SUFFIXES`.
  - Add real PDF fixtures from an actual source.
- **Scale:**
  - Add an HNSW index (`vector_cosine_ops`) in its own migration once the
    corpus reaches tens of thousands of chunks, and measure recall against
    exact search on the evaluation set.
  - pgvector indexes `vector` columns of up to 2,000 dimensions, so 1024 fits.
- **Hybrid retrieval:** add a PostgreSQL full-text column (`tsvector`) on
  `document_chunks` for Reciprocal Rank Fusion (ADR 001).
