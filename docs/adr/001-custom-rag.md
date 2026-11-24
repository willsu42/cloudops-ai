# ADR 001: Build a custom RAG pipeline

- **Status:** Accepted (planned; nothing implemented yet)
- **Date:** 2026-11-17

## Context

CloudOps AI will answer questions and support investigations with evidence
from documentation. The first corpus is the OpenTelemetry Demo documentation in
`datasets/corpus/`. A project goal is to learn and demonstrate parsing, chunking,
retrieval, fusion, and citation grounding directly, and to measure each stage.

## Decision

Implement retrieval-augmented generation in project code:

- Python parsing and heading-aware chunking.
- Embeddings through an `EmbeddingProvider` interface; Bedrock is the planned
  cloud implementation.
- Storage in PostgreSQL with pgvector, plus PostgreSQL full-text search.
- Reciprocal Rank Fusion for hybrid retrieval.
- Citation validation in code against immutable `document_versions`.

The Week 1 schema already prepares for this. Documents are identified by
source and revision, content hashes deduplicate, and the `vector` extension is
installed.

## Alternatives considered

- **Amazon Bedrock Knowledge Bases.** Less code and managed ingestion. But it
  hides chunking and retrieval behavior the project wants to study, makes
  per-stage evaluation harder, and ties local development to AWS.
- **A framework's built-in RAG components.** Faster to start, but much of the
  behavior under study would sit inside library defaults.

## Consequences

- More code to write and test: parsing edge cases, re-embedding on model
  change, deletion semantics.
- Retrieval quality can be measured per stage (Recall@5, MRR@5) and tuned on
  development data.
- Bedrock Knowledge Bases remains a possible comparison once release gates
  pass (stretch work in PROJECT_PLAN §2).
- Revisit if the corpus or operational load grows beyond what a single
  PostgreSQL instance handles well. ADR 003 (decision 6) defers an ANN index
  until the corpus needs one.
