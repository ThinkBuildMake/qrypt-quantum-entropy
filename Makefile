# Targets for the walking skeleton. `make help` lists them.
# Database selection (first match wins):
#   1. DATABASE_URL set in the environment or on the command line -> used as is ("external").
#   2. A reachable Docker daemon -> docker-compose.yml, Postgres 18 (the documented path).
#   3. Otherwise -> a database per worktree, qrypt_<dirname>, on the Postgres server at LOCAL_PG_ADMIN_URL
#      (a documented deviation: docs/DEVIATIONS.md).
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv run
LOCAL_PG_ADMIN_URL ?= postgresql://dev:dev@localhost:5432/postgres
COMPOSE_URL := postgresql://qrypt:qrypt@localhost:5432/qrypt
DB_NAME := qrypt_$(shell basename "$(CURDIR)" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9\n' '_')
HAS_DOCKER := $(shell docker info >/dev/null 2>&1 && echo yes || echo no)

ifneq ($(origin DATABASE_URL),undefined)
DB_MODE := external
else ifeq ($(HAS_DOCKER),yes)
DB_MODE := docker
DATABASE_URL := $(COMPOSE_URL)
else
DB_MODE := local
DATABASE_URL := $(shell $(UV) python -c "from sqlalchemy.engine import make_url; print(make_url('$(LOCAL_PG_ADMIN_URL)').set(database='$(DB_NAME)').render_as_string(hide_password=False))" 2>/dev/null)
endif
export DATABASE_URL

# Gate thresholds (override to prove the gate can fail).
GATE_P95_MS ?= 50
export GATE_P95_MS

.PHONY: help install db-url db-up db-down db-reset migrate spike-roles mcp-list mcp-call report test lint gate \
        check-imports check-language secret-scan run-http config-show

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

install: ## install locked dependencies on Python 3.14
	uv sync --frozen

db-up: ## start (or create) the dev database: Compose Postgres 18, else a per-worktree database
	@echo "database mode: $(DB_MODE)"
ifeq ($(DB_MODE),docker)
	docker compose up -d --wait db
else ifeq ($(DB_MODE),local)
	$(UV) python scripts/devdb.py up "$(DATABASE_URL)"
else
	$(UV) python -c "import psycopg,os; psycopg.connect(os.environ['DATABASE_URL']).close(); print('external database reachable')"
endif

db-down: ## stop the Compose database (no-op for a local server)
ifeq ($(DB_MODE),docker)
	docker compose down
endif

db-reset: ## back to the empty migrated state
ifeq ($(DB_MODE),docker)
	docker compose down -v
	docker compose up -d --wait db
else ifeq ($(DB_MODE),local)
	$(UV) python scripts/devdb.py drop "$(DATABASE_URL)"
	$(UV) python scripts/devdb.py up "$(DATABASE_URL)"
else
	$(UV) python -c "import psycopg,os; c=psycopg.connect(os.environ['DATABASE_URL'],autocommit=True); c.execute('DROP SCHEMA public CASCADE'); c.execute('CREATE SCHEMA public'); print('schema public reset')"
endif
	$(MAKE) migrate

db-url: ## print the DATABASE_URL in use
	@echo $(DATABASE_URL)

migrate: ## apply migrations (safe to run twice)
	$(UV) alembic upgrade head

spike-roles: ## spike S-1: insert-only roles work on DATABASE_URL (run as an admin)
	$(UV) python scripts/spike_roles.py

mcp-list: ## list the MCP tools of the stub server over stdio
	@rm -f logs/server.log
	$(UV) python scripts/mcp_client.py list

mcp-call: ## call a tool: make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'
	$(UV) python scripts/mcp_client.py call "$(TOOL)" '$(ARGS)'

run-http: ## serve the stub HTTP service locally on :8000
	$(UV) uvicorn qrypt_entropy.transport.http_app:app --port 8000

report: ## print the nine metrics panels
	$(UV) python -m qrypt_entropy.transport.report_cli

config-show: ## print the effective settings, secrets redacted
	@$(UV) python scripts/config_show.py

lint: ## ruff
	$(UV) ruff check .
	$(UV) ruff format --check .

test: ## offline test suite with coverage
	$(UV) pytest --cov --cov-report=term-missing:skip-covered

check-imports:
	$(UV) python tools/check_imports.py src

check-language:
	$(UV) python tools/check_language.py

secret-scan: ## gitleaks when installed, else the regex fallback; full history
	$(UV) python tools/secret_scan.py

gate: db-up migrate ## the regression gate: lint, tests + coverage + matrix + fuzz + p95, secret scan, imports, language
	$(MAKE) lint
	$(MAKE) check-imports
	$(MAKE) check-language
	$(MAKE) secret-scan
	$(MAKE) test
	@echo "GATE PASSED"
