# CloudOps AI — developer commands. Run `make help` for a list.
# Backend commands run inside Docker Compose; tests run on the host against a
# separate, disposable test database (docker-compose.test.yml).

PNPM ?= pnpm
COMPOSE := docker compose
TEST_COMPOSE := docker compose -f docker-compose.test.yml
TEST_DB_HOST_PORT ?= 55432
TEST_DATABASE_URL ?= postgresql+asyncpg://cloudops:cloudops@127.0.0.1:$(TEST_DB_HOST_PORT)/cloudops_test
BACKEND := apps/backend
FRONTEND := apps/frontend

.PHONY: help setup up down clean migrate seed ingest logs test test-db test-db-down \
        test-backend test-frontend lint fmt frontend

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install backend (uv) and frontend (pnpm) dependencies from lockfiles
	cd $(BACKEND) && uv sync --frozen
	cd $(FRONTEND) && $(PNPM) install --frozen-lockfile

up: ## Build and start db, run migrations, start the API (waits until healthy)
	$(COMPOSE) up --build --detach --wait db api

down: ## Stop containers; keeps the database volume
	$(COMPOSE) down

clean: ## DESTRUCTIVE: stop containers and delete the local database volume
	$(COMPOSE) down --volumes

migrate: ## Apply database migrations (inside Compose)
	$(COMPOSE) run --rm --build migrate

seed: ## Load services and corpus metadata (inside Compose; safe to repeat)
	$(COMPOSE) run --rm --build api python -m app.seed

ingest: ## Chunk, embed (mock provider), and store the corpus (inside Compose; run after seed)
	$(COMPOSE) run --rm --build api python -m app.ingest

logs: ## Follow API logs
	$(COMPOSE) logs -f api

test-db: ## Start the disposable test database
	$(TEST_COMPOSE) up --detach --wait

test-db-down: ## Remove the test database (data lives in tmpfs)
	$(TEST_COMPOSE) down

test: test-backend test-frontend ## Run all tests

test-backend: test-db ## Run backend tests against the test database
	cd $(BACKEND) && TEST_DATABASE_URL=$(TEST_DATABASE_URL) REQUIRE_DB_TESTS=1 uv run pytest

test-frontend: ## Run frontend tests
	cd $(FRONTEND) && $(PNPM) test

lint: ## Format check, lint, and type-check backend and frontend
	cd $(BACKEND) && uv run ruff format --check . && uv run ruff check . && uv run mypy app tests
	cd $(FRONTEND) && $(PNPM) lint && $(PNPM) typecheck

fmt: ## Auto-format and fix lint where possible
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .
	cd $(FRONTEND) && $(PNPM) lint --fix

frontend: ## Start the Vite dev server (proxies /health to the API)
	cd $(FRONTEND) && $(PNPM) dev
