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

# One .PHONY line per target: keeps parallel branches merge-safe (union merge).
.PHONY: agent
.PHONY: aiusage-check
.PHONY: agent-capture-check
.PHONY: agent-scripted
.PHONY: agent-tools
.PHONY: backup
.PHONY: chat
.PHONY: check-env
.PHONY: check-imports
.PHONY: check-language
.PHONY: check-routes
.PHONY: clean-clone-check
.PHONY: config-show
.PHONY: coverage
.PHONY: coverage-check
.PHONY: db-down
.PHONY: db-reset
.PHONY: db-up
.PHONY: eval-adversarial
.PHONY: eval-golden
.PHONY: eval-list
.PHONY: eval-summary
.PHONY: db-url
.PHONY: demo-chat
.PHONY: docs-check
.PHONY: demo-guard
.PHONY: demo-health
.PHONY: demo-issue
.PHONY: demo-operate
.PHONY: demo-redeem
.PHONY: fault-suite
.PHONY: fuzz
.PHONY: gate
.PHONY: guide-check
.PHONY: help
.PHONY: inspector-config
.PHONY: install
.PHONY: leak-scan
.PHONY: lint
.PHONY: load
.PHONY: load-concurrent
.PHONY: load-latency
.PHONY: mcp-call
.PHONY: mcp-list
.PHONY: migrate
.PHONY: mock-qrypt
.PHONY: readme-check
.PHONY: offline-triple
.PHONY: report
.PHONY: restore-drill
.PHONY: results
.PHONY: results-check
.PHONY: route-latency
.PHONY: run-http
.PHONY: sample-metrics
.PHONY: snippet-check
.PHONY: secret-scan
.PHONY: seed
.PHONY: seed-list-names
.PHONY: spike-roles
.PHONY: test
.PHONY: test-db-restart
.PHONY: trace
.PHONY: two-process-budget
.PHONY: help install db-url db-up db-down db-reset migrate spike-roles seed seed-list-names mock-qrypt demo-guard demo-issue demo-operate mcp-list mcp-call report sample-metrics test lint gate \
        agent-scripted agent-capture-check agent-tools test-db-restart trace backup restore-drill check-imports check-language secret-scan run-http config-show check-env guide-check readme-check snippet-check inspector-config clean-clone-check

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
	@echo "refusing: demo-issue, demo-operate and demo-redeem RESET the database in DATABASE_URL. Unset DATABASE_URL, or run with CONFIRM_RESET=1 if you mean it." >&2; exit 1
endif
endif

demo-issue: demo-guard db-reset ## S20 demo on a FRESH local database against the mock Qrypt (resets the database), prints the evidence
	$(UV) python scripts/demo_issue.py

demo-operate: demo-guard db-reset ## S34 demo on a FRESH local database against the mock Qrypt (resets the database): create and revoke clients, usage, budget cap
	$(UV) python scripts/demo_operate.py

demo-redeem: demo-guard db-reset ## S33 demo on a FRESH local database against the mock Qrypt (resets the database): issue, redeem, trace
	$(UV) python scripts/demo_redeem.py

demo-health: demo-guard db-reset ## S35 demo on a FRESH local database against the mock Qrypt (resets the database): health and metrics while the server runs, stops and restarts (~1 min)
	@mkdir -p logs
	$(UV) python scripts/demo_health.py

mcp-list: ## list the MCP tools over stdio
	@rm -f logs/server.log
	$(UV) python scripts/mcp_client.py list

mcp-call: db-up migrate ## call a tool (needs CLIENT_KEY from make seed, and Qrypt or make mock-qrypt): make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'
	$(UV) python scripts/mcp_client.py call "$(TOOL)" '$(ARGS)'

run-http: ## serve the HTTP service locally on :8000
	$(UV) uvicorn qrypt_entropy.transport.http_app:app --port 8000

trace: ## print the provenance of one issuance: make trace REF=<uuid>, or by token: TRACE_VALUE=<code> make trace OWNER=<owner>
	@$(UV) python scripts/trace.py "$(REF)" "$(OWNER)"

chat: db-up migrate ## S32: the interactive agent with the REAL OpenRouter model against the local stack (docs/DEPLOY.md S32); needs OPENROUTER_API_KEY, MCP_CLIENT_KEY, KEY_HASH_SECRET, PEPPER, QRYPT_BASE_URL, QRYPT_API_KEY
	$(UV) python scripts/chat_local.py

agent: chat ## same as chat

demo-chat: demo-guard db-reset ## S32 demo on a FRESH local database (resets it): real MCP server, mock Qrypt, scripted model over a local OpenAI-compatible stub
	@mkdir -p logs && PYTHONPATH=. $(UV) python scripts/demo_chat.py

agent-scripted: ## agent chat with the scripted model and a stub tool: make agent-scripted PROMPT='...'
	@mkdir -p logs && $(UV) python -m qrypt_entropy.agent scripted "$(PROMPT)"

agent-capture-check: ## check a saved model-request capture holds placeholders only: make agent-capture-check [CAPTURE=logs/agent-capture.json]
	$(UV) python -m qrypt_entropy.agent capture-check "$(or $(CAPTURE),logs/agent-capture.json)"

agent-tools: db-up migrate ## list the tools the agent gets from the stub MCP server (needs KEY_HASH_SECRET and PEPPER)
	$(UV) python scripts/seed_stubs.py && $(UV) python scripts/agent_tools.py

eval-list: ## S40 AC1: the 15 golden and 5 adversarial prompts with their expected tools and argument rules
	$(UV) python scripts/eval_agent.py list

eval-golden: db-up ## S40 AC2, AC3, AC5, AC7: the 15 prompts; the REAL model when OPENROUTER_API_KEY is set (results/eval/), else the stub (results/eval_stub/)
	$(UV) python scripts/eval_agent.py golden
	$(MAKE) agent-capture-check

eval-adversarial: db-up ## S40 AC4: the 5 adversarial prompts (same model choice as eval-golden)
	$(UV) python scripts/eval_agent.py adversarial

eval-summary: ## S40 AC6: model, free-tier limit and total requests used by the two runs
	$(UV) python scripts/eval_agent.py summary

report: ## print the nine metrics panels: make report [LOG=calls.log] [SNAPSHOTS=snaps.json]
	$(UV) python -m qrypt_entropy.transport.report_cli

sample-metrics: ## scrape /metrics into snaps.json: make sample-metrics SECONDS=12 INTERVAL=5 [METRICS_URL= ADMIN_KEY= SNAPSHOTS=]
	$(UV) python -m qrypt_entropy.transport.metrics_sampler

BACKUP_DIR ?= backups
backup: ## daily backup: pg_dump as the backup role, encrypted with age; needs BACKUP_URL and BACKUP_AGE_RECIPIENT (docs/BACKUP.md)
	$(UV) python scripts/backup.py "$(BACKUP_DIR)"

restore-drill: ## restore the newest backup into a fresh Postgres and re-run the migrations; needs AGE_IDENTITY (docs/BACKUP.md)
	$(UV) python scripts/restore_drill.py

load-latency: db-up ## S36 PF-1: 1,000 warm-pool calls over stdio, p50/p95/p99 -> results/load_latency.json (own throwaway database; ~15 s)
	$(UV) python scripts/load_test.py latency

load-concurrent: db-up ## S36 PF-2..PF-3, DE-1, SC-1, VR-1, VR-9: 100 callers for 30 s and 10,000 calls -> results/load_concurrent.json (~50 s)
	$(UV) python scripts/load_test.py concurrent

load: load-latency load-concurrent ## both load runs
fault-suite: db-up migrate ## S39: every Qrypt fault (403, 401, 429, 5xx, slow, dropped, malformed) fails closed through the real service
	$(UV) pytest tests/gate/test_fault_suite_gate.py --no-cov -q

two-process-budget: db-up migrate ## S39: two server processes share one cap; a 429 retry stays one batch
	$(UV) pytest tests/gate/test_two_process_budget_gate.py --no-cov -q

# S41: the 80% coverage line (EV-5) and the results file (EV-6).
COVERAGE_MIN ?= 80
FILE ?= results/RESULTS.md

coverage-check: ## S41 AC1: core-logic coverage from the last `make coverage` run against COVERAGE_MIN (default 80); exits 1 below it
	$(UV) python -m tools.coverage_check --min $(COVERAGE_MIN)

coverage: db-up migrate ## S41 AC1: full suite with branch coverage, then the core-logic check; fails below COVERAGE_MIN (default 80): make coverage COVERAGE_MIN=99
	$(UV) pytest --cov --cov-fail-under=0 --cov-report=json:results/coverage_report.json --cov-report=term:skip-covered -q
	$(MAKE) coverage-check COVERAGE_MIN=$(COVERAGE_MIN)

results: ## S41 AC2, AC3: write results/RESULTS.md from coverage, load and real-model evaluation results (results/eval/, never eval_stub/)
	$(UV) python scripts/make_results.py

results-check: ## S41 AC2 to AC4: the results file holds every required section; FILE=other.md checks another file
	$(UV) python -m tools.results_check $(FILE)

offline-triple: db-up migrate ## S41 AC5: the unit suite 3 times, outbound network blocked, no Qrypt or model key: identical results, 0 network calls
	$(UV) python scripts/offline_triple.py

config-show: ## print the effective settings, secrets redacted
	@$(UV) python scripts/config_show.py

lint: ## ruff
	$(UV) ruff check .
	$(UV) ruff format --check .

test: ## offline test suite with coverage
	$(UV) pytest --cov --cov-report=term-missing:skip-covered

test-db-restart: ## opt-in: really restart the local Postgres (disrupts other worktrees; not part of the gate)
	ALLOW_DB_RESTART=1 $(UV) pytest tests/test_dbrecover.py -k 'restart or survive' --no-cov

ROBUST := tests/gate/test_robust_surface_gate.py --no-cov -s -q -p no:logging --show-capture=no

fuzz: db-up migrate ## S37 AC2: 1,000 malformed inputs across every tool and route; 0 unhandled errors, 0 5xx
	$(UV) pytest $(ROBUST) -k fuzz

check-routes: db-up migrate ## S37 AC4: each hosted route called once with the right key; none returns an entropy-derived value
	$(UV) pytest $(ROBUST) -k hosted_route

route-latency: db-up migrate ## S37 AC5: 100 requests per hosted route, p95 <= 1 s, figures in results/route_latency.json (ROUTE_LATENCY_FILE)
	$(UV) pytest $(ROBUST) -k route_latency

leak-scan: db-up migrate ## S38: full run with known markers, then search files, logs, responses, tables, dump, tree and git history
	@echo "constraints checked: QRYPT_API_KEY is never committed (CP-1); pooled entropy is never on disk in plaintext (CP-2); no value, key or secret is logged (OB-2, OB-3)"
	$(UV) pytest tests/gate/test_leak_scan_gate.py --no-cov -s -q -p no:logging --show-capture=no
	$(MAKE) secret-scan

check-imports:
	$(UV) python tools/check_imports.py src

check-env: ## .env.example lists every setting with placeholders only
	$(UV) python -m tools.check_env

guide-check: ## docs/DEPLOYMENT_GUIDE.md covers the required steps and names only real targets, scripts and variables
	$(UV) python -m tools.guide_check

docs-check: ## S42: docs/technical has every section, matches the routes and tools, its diagrams parse, no secret
	$(UV) python -m tools.docs_check
readme-check: ## README first line <= 120 characters, only real commands, scripts and variables, snippet equals docs/mcp-client.json
	$(UV) python -m tools.readme_check

aiusage-check: ## S45: AI_USAGE.md has the tools, models, purposes and configuration, names only files that exist, no secret
	$(UV) python -m tools.aiusage_check

snippet-check: ## launch an MCP client from the README snippet (throwaway database, mock Qrypt) and list the three tools
	$(UV) python scripts/snippet.py check

inspector-config: ## write the README snippet with the values in your shell to logs/inspector.json (needs the README exports)
	$(UV) python scripts/snippet.py fill logs/inspector.json

clean-clone-check: ## DP-1: clone HEAD, follow only the README (quickstart, then Inspector), print the elapsed times (SKIP_GATE=1 skips the gate)
	$(UV) python scripts/clean_clone_check.py

check-language:
	$(UV) python tools/check_language.py

secret-scan: ## gitleaks when installed, else the regex fallback; full history
	$(UV) python tools/secret_scan.py

gate: db-up migrate ## the regression gate: lint, tests + coverage + matrix + fuzz + p95, secret scan, imports, language
	$(MAKE) lint
	$(MAKE) check-imports
	$(MAKE) check-language
	$(MAKE) check-env
	$(MAKE) guide-check
	$(MAKE) docs-check
	$(MAKE) readme-check
	$(MAKE) aiusage-check
	$(MAKE) snippet-check
	$(MAKE) secret-scan
	$(MAKE) results-check
	$(MAKE) test
	@echo "GATE PASSED"
