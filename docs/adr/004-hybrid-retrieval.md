# ADR 004: Hybrid retrieval with PostgreSQL full-text search and RRF

- **Status:** Accepted (implemented in Week 2, after phase 2D)
- **Date:** 2026-11-25

## Context

Phase 2D added exact vector retrieval with a cosine-distance cap. Until AWS is
enabled, the only embedding provider is a deterministic hash mock (ADR 003,
decision 4). The mock's vectors carry no meaning. Distinct texts land at
cosine distance of about 1.0, so with the default cap of 0.8 a natural-language
question retrieves nothing. Only text nearly identical to a stored chunk
matches.

As a result:

- Phase 2E's ask endpoint would always abstain under mock.
- Phase 2H's run of at least 10 dev questions would exercise only the abstain
  path.

ADR 001 already planned hybrid retrieval: PostgreSQL full-text search plus
vectors, fused with Reciprocal Rank Fusion (RRF). On 2026-11-25 the user chose
to implement the lexical half now, so the pipeline has meaningful retrieval
before any paid model is available.

## Decision

### 1. A generated `tsvector` column with a GIN index

Migration `0003` adds `document_chunks.search_vector`, defined as
`GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED`, and
indexes it with a GIN index.

- PostgreSQL maintains the column, so ingestion is unchanged and the column
  can never drift from `text`. Adding the column also backfills rows that
  already exist.
- The text search configuration (`english`) is named explicitly. That makes the
  expression immutable, which generated columns require, and results don't
  depend on the server's `default_text_search_config`.

### 2. Lexical query: free text, any-term match, `ts_rank`

- `plainto_tsquery` parses the question as plain text. User input can never
  inject tsquery operators. Stop words are dropped and terms are stemmed.
- Its `&` (AND) operators are rewritten to `|` (OR). A chunk qualifies if it
  contains any query term. Full questions rarely have every term in one
  chunk, so AND would mostly return nothing.
- Matches are ordered by `ts_rank` with normalization 1, which divides by
  `1 + log(length)` so long chunks do not win by bulk. Ties break by chunk ID.

### 3. Fusion with Reciprocal Rank Fusion, k = 60

- Each channel (vector, lexical) returns up to `RETRIEVAL_CANDIDATES`
  candidates (default 20), with every filter applied inside the ranking query:
  active set, service, and the vector distance cap.
- Each chunk's fused score is `sum(1 / (60 + rank))` over the channels that
  returned it. The final list keeps `RETRIEVAL_TOP_K` (default 5).
- RRF uses ranks only, so a cosine distance never has to be compared with a
  `ts_rank` value. k = 60 is the usual constant from Cormack et al. (2009).
- Each result keeps its channel scores (`vector_distance`, `lexical_score`)
  for debugging and evaluation.

### 4. An empty result still means "no evidence"; a non-empty one is only a candidate

- Retrieval returns `[]` when neither channel matches. Callers must then
  abstain without calling a model.
- A non-empty result is not proof that the question is answerable. An OR
  query over the domain's own vocabulary ("checkout", "service") matches
  something for almost any in-domain question. That includes the two
  `insufficient_evidence` dev cases. Abstaining in those cases is the job of
  the answer step (phase 2E): claims must be supported by the cited chunks,
  and unsupported claims are dropped.

## Alternatives considered

- **Keep vector-only retrieval and accept abstain-only under mock.** This is
  honest but leaves 2E–2H mostly untestable until AWS is available. Rejected.
- **A test-only path that feeds hand-picked chunks to the mock LLM.** It tests
  the answer step but not retrieval, and its fixtures could drift from real
  behavior. Rejected.
- **AND semantics (`plainto_tsquery` or `websearch_to_tsquery` as is).** More
  precise, but complete questions almost never match in full. Rejected for
  recall.
- **BM25 through an extension (e.g. `pg_search`) or an external engine.**
  Better ranking, but it adds a dependency and an extension that the managed
  PostgreSQL in the AWS plan may not offer. Deferred.
- **A weighted sum of normalized scores instead of RRF.** It needs per-channel
  calibration that would have to be redone for every model. Rejected.
- **Indexing heading paths with a higher weight.** Headings already appear in
  the first chunk of each section. It is also awkward to do in a generated
  column (`array_to_string` is not immutable). Deferred.

## Consequences

- Retrieval now finds relevant chunks for natural-language questions under
  mock. An ad-hoc check on 2026-11-25 put a relevant document in the top 5 for
  7 of the 9 answerable dev cases. **This is development data only and not a
  quality claim.** Both misses expected `architecture.md`, whose service
  diagram is one large chunk that length normalization ranks low.
- Under mock, every hybrid result comes from the lexical channel. The vector
  channel's contribution can only be measured once a real embedding model is
  enabled. The fusion is ready for it.
- English-only stemming. Non-English documents would need another
  configuration, either per document or per chunk.
- Two queries per retrieval, both exact. That is fine at about 100 chunks;
  see ADR 003, decision 6, for when to add an ANN index.

## Future extensions

- Tune `RETRIEVAL_CANDIDATES`, RRF k, and the distance cap on the dev set once
  real embeddings exist. Report Recall@5 and MRR@5 per channel and fused.
- Weight headings (`setweight`), for example through an ingest-written
  `heading_text` column.
- Try BM25 if lexical ranking turns out to be the bottleneck.
- Add a minimum-coverage rule: require a share of the query terms to match
  before a lexical hit counts as evidence, if the answer step needs a stronger
  signal for abstaining.
