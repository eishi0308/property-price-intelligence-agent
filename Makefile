# Convenience targets. Everything here is a thin wrapper around a command you can
# also run directly — nothing is hidden behind make.

BACKEND := backend
PY      := $(BACKEND)/.venv/bin/python
PIP     := $(BACKEND)/.venv/bin/pip
RUFF    := $(BACKEND)/.venv/bin/ruff

.DEFAULT_GOAL := help
.PHONY: help setup db-create schema seed embeddings feasibility api web dev \
        test test-unit lint format evals check docker-up docker-down clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the backend venv, install deps, install frontend deps
	python3.11 -m venv $(BACKEND)/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -r $(BACKEND)/requirements-dev.txt
	cd frontend && npm install

db-create: ## Create the local database and enable pgvector
	createdb property_intel || true
	psql -d property_intel -c "CREATE EXTENSION IF NOT EXISTS vector;"

seed: ## Load the demo corpus into PostgreSQL (drops and recreates tables)
	cd $(BACKEND) && .venv/bin/python scripts/seed_demo_data.py --reset

embeddings: ## Build the pgvector semantic index
	cd $(BACKEND) && .venv/bin/python scripts/build_embeddings.py

feasibility: ## PHASE 1 GATE — can the configured provider supply what we need?
	cd $(BACKEND) && .venv/bin/python scripts/check_property_data.py

fixtures: ## Regenerate the demo corpus and the retrieval golden dataset
	cd $(BACKEND) && .venv/bin/python scripts/generate_fixtures.py
	cd $(BACKEND) && .venv/bin/python scripts/build_golden_dataset.py

api: ## Run the FastAPI backend on :8000
	cd $(BACKEND) && .venv/bin/python -m uvicorn app.main:app --reload --port 8000

web: ## Run the Next.js frontend on :3000
	cd frontend && npm run dev

bootstrap: db-create seed embeddings ## One-shot local setup after `make setup`

test: ## Run the whole test suite
	cd $(BACKEND) && .venv/bin/python -m pytest tests -q

test-unit: ## Run only the tests that need no database
	cd $(BACKEND) && .venv/bin/python -m pytest tests/unit -q

evals: ## Run the retrieval, RAG and agent evaluations
	cd $(BACKEND) && .venv/bin/python -m evals.run

lint: ## Lint and format-check the backend, typecheck the frontend
	cd $(BACKEND) && .venv/bin/ruff check app scripts evals tests
	cd $(BACKEND) && .venv/bin/ruff format --check app scripts evals tests
	cd frontend && npx tsc --noEmit

format: ## Auto-format the backend
	cd $(BACKEND) && .venv/bin/ruff check --fix app scripts evals tests
	cd $(BACKEND) && .venv/bin/ruff format app scripts evals tests

check: lint test evals ## Everything CI runs

docker-up: ## Start the full stack in Docker
	docker compose up --build

docker-down: ## Stop the stack and remove volumes
	docker compose down -v

clean: ## Remove caches and build artefacts
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache frontend/.next
