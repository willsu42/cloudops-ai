# CloudOps AI

An evidence-grounded incident investigation assistant, under construction. The reference
system is the [OpenTelemetry Demo](https://opentelemetry.io/docs/demo/), starting
with its checkout flow.

> **Status: Week 1 foundation, plus Week 2 phases 2A–2E.** This repository currently contains a local
> application skeleton (API health checks, database schema, seed data, a status
> page), a document corpus, and an ingestion pipeline that stores chunks with
> **mock** embeddings, hybrid (full-text + vector) retrieval, a source lookup API,
> and a question endpoint whose answers are grounded in citations, using a quoting
> **mock** model. It does **not** yet
> investigate incidents, call a real model, or deploy to AWS. See
> [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) for the roadmap.

## What exists

| Part | Description |
| --- | --- |
| API ([apps/backend](apps/backend)) | FastAPI with `GET /health/live`, `GET /health/ready`, `GET /api/sources/{chunk_id}`, and `POST /api/ask`. Readiness checks PostgreSQL and the `vector` extension under a short deadline. The source endpoint returns a stored chunk's text, heading path, line range, and document version. It returns 404 unless the chunk is in an active set. |
| Database | PostgreSQL 17 + pgvector, migrated with Alembic. Tables: `services`, `documents`, `document_versions`, plus `ingestion_runs`, `chunk_sets`, `document_chunks` (`vector(1024)`). |
| Corpus ([datasets/corpus](datasets/corpus)) | 12 OpenTelemetry Demo documentation pages (architecture, Docker deployment, the checkout path from frontend through quote and Kafka), copied unmodified with provenance. |
| Seed | `python -m app.seed` loads services and document metadata. Safe to repeat. |
| Parsing and chunking ([apps/backend/app/rag](apps/backend/app/rag)) | Pure functions (phase 2B). They turn corpus Markdown into heading-aware chunks with line ranges. |
| Ingestion ([apps/backend/app/ingest.py](apps/backend/app/ingest.py)) | `python -m app.ingest` chunks and embeds every corpus document and stores 104 chunks. Repeat runs skip unchanged documents. A failed run keeps the previous chunks active. Only Markdown is accepted. Embeddings come from a deterministic **mock** with no semantic meaning. See [ADR 003](docs/adr/003-chunk-storage-and-embeddings.md). |
| Retrieval ([apps/backend/app/rag/retrieval.py](apps/backend/app/rag/retrieval.py)) | Hybrid search over active chunks: PostgreSQL full-text (English stemming, any-term match) and exact cosine vector search, fused with Reciprocal Rank Fusion. It has an optional service filter and a vector distance cap. An empty result means "no evidence". `POST /api/ask` uses it. See [ADR 004](docs/adr/004-hybrid-retrieval.md). |
| Ask ([apps/backend/app/rag/answer.py](apps/backend/app/rag/answer.py)) | `POST /api/ask {"question": ..., "services": [...]}` returns:<br>- claims, each citing retrieved passages<br>- every retrieved passage<br>- limitations<br>- an `abstained` flag<br>Claims that cite passages not retrieved for this request are removed. With no evidence, it abstains without calling the model. The mock model only quotes sentences from the sources. See [ADR 005](docs/adr/005-grounded-answers-and-abstention.md). |
| Evaluation ([datasets/evaluation](datasets/evaluation)) | Case schema, split rules, and 11 labeled development cases. No held-out test set yet. |
| Frontend ([apps/frontend](apps/frontend)) | React page showing live backend readiness. |
| CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) | Lint, type checks, tests (backend on pgvector), frontend build, secret scanning. |

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Docker | Docker Engine with Compose v2 | Docker Desktop works. Must be running. |
| uv | 0.11.x | <https://docs.astral.sh/uv/getting-started/installation/>. It installs Python 3.12 if needed. |
| Node.js | 24 (LTS) | See [apps/frontend/.nvmrc](apps/frontend/.nvmrc). Node 25 also satisfies `engines`, but CI uses 24. |
| pnpm | 12.17.0 | Pinned in `apps/frontend/package.json` (`packageManager`). Install steps below. |
| make | any | Optional. Each target is a short command you can run by hand. |

**Installing pnpm.** Use one of these:

```shell
# Option A: Corepack (bundled with Node; needs write access to Node's bin directory)
corepack enable pnpm

# Option B: npm global install
npm install -g pnpm@12.17.0

# Option C: no global install; pass this to make
make PNPM="npx --yes pnpm@12.17.0" setup
```

Check with `pnpm --version` (expect `12.17.0`). With Corepack, the pinned version
is selected automatically from `packageManager`.

## Quick start

From a fresh clone:

```shell
make setup      # install backend (uv) and frontend (pnpm) dependencies from lockfiles
make up         # build the API image, start PostgreSQL, run migrations, start the API
make seed       # load services and corpus metadata (repeatable)
make ingest     # chunk, embed (mock), and store the corpus (repeatable)
make frontend   # start the Vite dev server at http://localhost:5173
```

Then:

- <http://localhost:5173>: status page. It should say **Ready**.
- <http://localhost:8000/health/ready>: `{"status":"ready","checks":{"database":"ok","vector_extension":"ok"}}`
- <http://localhost:8000/docs>: generated API docs.

No AWS credentials are needed. Nothing calls paid services.

Stop with `make down`, which keeps the database volume. `make clean` **deletes** the
local database volume.

## Configuration

Docker Compose reads an optional `.env` file in the repository root. Copy
[.env.example](.env.example) if you need to change defaults.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROVIDER_MODE` | `mock` | Only `mock` is supported. `aws` fails at startup with an explanation. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `API_HOST_PORT` | `8000` | Host port for the API. The Vite proxy uses it too. |
| `DB_HOST_PORT` | `5433` | Host port for the development database (inspection only). |
| `TEST_DB_HOST_PORT` | `55432` | Host port for the test database. |

The API container itself also reads these (set in
[docker-compose.yml](docker-compose.yml)):

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | none (required) | Must use `postgresql+asyncpg://`. |
| `DB_CONNECT_TIMEOUT_SECONDS` | `2.0` | Deadline for opening a DB connection. |
| `READINESS_TIMEOUT_SECONDS` | `3.0` | Overall deadline for `/health/ready`. |
| `DATASETS_DIR` | `<repo>/datasets` | `/srv/datasets` in the container (read-only mount). |
| `RETRIEVAL_TOP_K` | `5` | Maximum chunks returned per query (1–50). Not set in Compose; the default applies. |
| `RETRIEVAL_MAX_DISTANCE` | `0.8` | Cosine-distance cap (0–2) for the vector channel. Not set in Compose; the default applies. To be tuned with a real embedding model. |
| `RETRIEVAL_CANDIDATES` | `20` | Candidates each channel (vector, full-text) returns before fusion (1–200). Not set in Compose; the default applies. |

**Database addresses.** Inside Compose the database is `db:5432`. From your
machine the same database is `127.0.0.1:5433`
(`psql postgresql://cloudops:cloudops@127.0.0.1:5433/cloudops`). Backend
commands in the Makefile (`migrate`, `seed`) run inside Compose, so you never
need a host-side `DATABASE_URL`. The `cloudops`/`cloudops` credential is a
local-only development default.

## Tests and checks

```shell
make test       # backend tests (starts the disposable test DB first) + frontend tests
make lint       # ruff format check, ruff lint, mypy, eslint, tsc
make fmt        # auto-format / auto-fix
```

Backend tests run on the host against a **separate** database from
[docker-compose.test.yml](docker-compose.test.yml): project
`cloudops-ai-test`, port 55432, database `cloudops_test`, stored in tmpfs. The
test suite refuses to run against any database whose name does not end in
`_test`, and it never touches the development database. `make test-db-down`
removes the test database.

If `TEST_DATABASE_URL` is unset, database tests are skipped. `make test` and CI
set `REQUIRE_DB_TESTS=1`, which turns that skip into a failure.

Main backend test coverage:

- configuration errors name the bad variable
- readiness against real pgvector
- a refused or hanging database gives a bounded 503 while liveness stays up
- outage and recovery through a toggling TCP proxy
- repeat seeding keeps identical rows
- ingestion: repeat runs keep chunk IDs, failed embeddings keep the previous active chunks, model switches swap atomically, changed files and PDFs are refused
- corpus manifest and evaluation split rules

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot connect to the Docker daemon` | Start Docker Desktop, then retry. |
| `make up` says a port is already allocated | Another process uses 8000 or 5433. Set `API_HOST_PORT` / `DB_HOST_PORT` in `.env`. |
| Status page shows **API unreachable** | The API is not running or not on `API_HOST_PORT`. Run `make up`, then `docker compose ps`. |
| Status page shows **Unavailable** | The API runs but the database is not ready. Check `docker compose ps db` and `make logs`. |
| `pnpm: command not found` | Install pnpm (see Prerequisites) or use `make PNPM="npx --yes pnpm@12.17.0" ...`. |
| uv warns `VIRTUAL_ENV ... does not match` | Another virtualenv (e.g. Conda) is active. Run `unset VIRTUAL_ENV` or deactivate it. Harmless otherwise. |
| `Invalid configuration: ... PROVIDER_MODE` | Only `mock` is supported until the Bedrock adapter passes a real smoke test. |
| Ingest reports `no seeded document version matches` | Run `make seed` first. If you did, a corpus file changed: restore it, or record a new revision. |
| Seed fails with a unique-constraint error on `document_versions` | A corpus file changed without a new `source_revision` in the manifest. Restore the file or record the new revision. |

## Repository layout

```text
apps/backend/     FastAPI app, Alembic migrations, tests (uv project)
apps/frontend/    React + Vite status page (pnpm project)
datasets/corpus/  Knowledge corpus + manifest (ingested by `make ingest`)
datasets/evaluation/  Evaluation cases (never ingested)
docs/             Project plan, architecture, ADRs
```

## Current limitations

- Embeddings are mock vectors only. Under mock, hybrid results come entirely from full-text search, and the vector channel matches only near-identical text. No retrieval-quality claims are made.
- Answers come from a mock that quotes source sentences, not from a language model. It abstains often by design. Citations are checked to belong to the retrieved passages, not checked for entailment.
- No real model calls yet.
- Only Markdown is ingested. PDF and other formats are rejected with an explicit error.
- No authentication. The API is bound to `127.0.0.1` and meant for local use only.
- No investigation workflow, tools, jobs, or worker process.
- No AWS infrastructure or deployment.
- The evaluation set is small and development-only. It supports no quality claims.

## Documentation

- [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md): eight-week plan (see its 2026-11-17 revision note)
- [docs/architecture.md](docs/architecture.md): implemented local architecture vs. target
- [docs/adr/](docs/adr/): architecture decision records (003: chunk storage, embedding dimension, accepted formats; 004: hybrid retrieval; 005: grounded answers and abstention)
- [datasets/corpus/README.md](datasets/corpus/README.md): corpus sources and attribution

## License

The project license has **not been chosen yet**. Until one is added, no license
is granted for the project's own code. The files in `datasets/corpus/otel-demo/`
are © OpenTelemetry Authors under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). See
[datasets/corpus/README.md](datasets/corpus/README.md).
