.DEFAULT_GOAL := help
VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: help venv install dev test cov lint fmt migrate simulate openapi docker-up docker-down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtualenv
	test -d $(VENV) || python3 -m venv $(VENV)

install: venv ## Install with dev extras
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

dev: ## API with reload on :8000
	$(VENV)/bin/uvicorn saas_billing.main:app --reload --port 8000

migrate: ## Apply Alembic migrations to DATABASE_URL
	$(VENV)/bin/alembic upgrade head

simulate: ## Run the 3-month / 5-customer simulation
	$(PY) scripts/simulate.py

test: ## Run tests
	$(PY) -m pytest

cov: ## Tests with coverage
	$(PY) -m pytest --cov=saas_billing --cov-report=term-missing

lint: ## Ruff check + format check
	$(VENV)/bin/ruff check saas_billing tests scripts alembic
	$(VENV)/bin/ruff format --check saas_billing tests scripts alembic

fmt: ## Format + autofix
	$(VENV)/bin/ruff format saas_billing tests scripts alembic
	$(VENV)/bin/ruff check --fix saas_billing tests scripts alembic

openapi: ## Export the OpenAPI document to openapi.json
	$(PY) scripts/export_openapi.py

docker-up: ## api + postgres
	docker compose up --build -d

docker-down: ## Stop stack
	docker compose down

clean: ## Remove caches and local DB
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage billing.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
