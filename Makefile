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

.PHONY: help install db-url db-up db-down db-reset migrate spike-roles seed seed-list-names mock-qrypt demo-guard demo-issue mcp-list mcp-call report sample-metrics test lint gate \
        agent-scripted agent-capture-check agent-tools test-db-restart trace backup restore-drill check-imports check-language secret-scan run-http config-show check-env

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

seed: ## create 1 admin, 2 clients and 3 seed tokens (keys printed once; needs KEY_HASH_SECRET and TOKEN_PEPPER)
	$(UV) python scripts/seed.py

seed-list-names: ## list the seeded credential names and seed-token owners
	$(UV) python scripts/seed.py list-names

mock-qrypt: ## serve the committed mock Qrypt on 127.0.0.1:$(MOCK_PORT) (default 8099); QRYPT_BASE_URL=http://127.0.0.1:8099 QRYPT_API_KEY=$(MOCK_QRYPT_TOKEN)
	$(UV) python -m tests.mock_qrypt --port $(or $(MOCK_PORT),8099)

demo-guard:
ifeq ($(DB_MODE),external)
ifneq ($(CONFIRM_RESET),1)
	@echo "refusing: demo-issue RESETS the database in DATABASE_URL. Unset DATABASE_URL, or run with CONFIRM_RESET=1 if you mean it." >&2; exit 1
endif
endif

demo-issue: demo-guard db-reset ## S20 demo on a FRESH local database against the mock Qrypt (resets the database), prints the evidence
	$(UV) python scripts/demo_issue.py

mcp-list: ## list the MCP tools over stdio
	@rm -f logs/server.log
	$(UV) python scripts/mcp_client.py list

mcp-call: db-up migrate ## call a tool (needs CLIENT_KEY from make seed, and Qrypt or make mock-qrypt): make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'
	$(UV) python scripts/mcp_client.py call "$(TOOL)" '$(ARGS)'

run-http: ## serve the HTTP service locally on :8000
	$(UV) uvicorn qrypt_entropy.transport.http_app:app --port 8000

trace: ## print the provenance of one issuance: make trace REF=<uuid>
	@$(UV) python scripts/trace.py "$(REF)"

agent-scripted: ## agent chat with the scripted model and a stub tool: make agent-scripted PROMPT='...'
	@mkdir -p logs && $(UV) python -m qrypt_entropy.agent scripted "$(PROMPT)"

agent-capture-check: ## check a saved model-request capture holds placeholders only: make agent-capture-check [CAPTURE=logs/agent-capture.json]
	$(UV) python -m qrypt_entropy.agent capture-check "$(or $(CAPTURE),logs/agent-capture.json)"

agent-tools: db-up migrate ## list the tools the agent gets from the stub MCP server (needs KEY_HASH_SECRET and PEPPER)
	$(UV) python scripts/seed_stubs.py && $(UV) python scripts/agent_tools.py

report: ## print the nine metrics panels: make report [LOG=calls.log] [SNAPSHOTS=snaps.json]
	$(UV) python -m qrypt_entropy.transport.report_cli

sample-metrics: ## scrape /metrics into snaps.json: make sample-metrics SECONDS=12 INTERVAL=5 [METRICS_URL= ADMIN_KEY= SNAPSHOTS=]
	$(UV) python -m qrypt_entropy.transport.metrics_sampler

BACKUP_DIR ?= backups
backup: ## daily backup: pg_dump as the backup role, encrypted with age; needs BACKUP_URL and BACKUP_AGE_RECIPIENT (docs/BACKUP.md)
	$(UV) python scripts/backup.py "$(BACKUP_DIR)"

restore-drill: ## restore the newest backup into a fresh Postgres and re-run the migrations; needs AGE_IDENTITY (docs/BACKUP.md)
	$(UV) python scripts/restore_drill.py

config-show: ## print the effective settings, secrets redacted
	@$(UV) python scripts/config_show.py

lint: ## ruff
	$(UV) ruff check .
	$(UV) ruff format --check .

test: ## offline test suite with coverage
	$(UV) pytest --cov --cov-report=term-missing:skip-covered

test-db-restart: ## opt-in: really restart the local Postgres (disrupts other worktrees; not part of the gate)
	ALLOW_DB_RESTART=1 $(UV) pytest tests/test_dbrecover.py -k 'restart or survive' --no-cov

check-imports:
	$(UV) python tools/check_imports.py src

check-env: ## .env.example lists every setting with placeholders only
	$(UV) python -m tools.check_env

check-language:
	$(UV) python tools/check_language.py

secret-scan: ## gitleaks when installed, else the regex fallback; full history
	$(UV) python tools/secret_scan.py

gate: db-up migrate ## the regression gate: lint, tests + coverage + matrix + fuzz + p95, secret scan, imports, language
	$(MAKE) lint
	$(MAKE) check-imports
	$(MAKE) check-language
	$(MAKE) check-env
	$(MAKE) secret-scan
	$(MAKE) test
	@echo "GATE PASSED"
