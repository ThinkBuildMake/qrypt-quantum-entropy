### S01 — Walking skeleton: the whole system running, deployed, with stubs, a regression gate and an empty metrics report

**Kind:** skeleton
**Blocked by:** none
**Review gate:** human demo
**Size:** L — deploy, the gate, every table, every stub and the report in one story; first contact with the hosting platform and the database roles spike (agent 45–75 min)
**Builds:** Contract stubs for every MCP tool, HTTP route and module interface in ARCHITECTURE.md; All eight tables created by migrations (write paths are built by the owning module stories); Deployment of the stub HTTP service on the free host and the database roles spike; Migrations: schema only from migrations, safe to run twice, tables created; Layering: no module imports a transport; cross-module table changes only through interfaces; Test doubles: the offline suite harness against Compose Postgres 18 (the mock Qrypt and the scripted model are owned by their modules); Regression gate on every push: lint, tests with coverage, secret scan; Environments: local/CI and demo/submission, deployed on Render; Bootstrap: the migration creates the one budget row (cap 10,240 KiB, used 0); Metrics place: one panel per KEY_METRICS metric (CLI report plus /metrics)
**Extends:** none
**Stubs:** Every module interface and every tool and route: synthetic contract-shaped data, each removed by the wiring story that wires its flow (S20, S32, S33, S34, S35).
**Requirements:** AZ-1, CH-2, CP-1, CP-4, DP-3, DP-5, EV-4, FR-3.3, GC-5, GC-6, GC-9, PF-1, RL-5
**Guards:** PF-1, EV-4, CP-1, CH-2

#### Why
Gives every later story a deployed system, all eight tables, a contract stub for every tool, route and module interface, a regression gate that must pass before anything merges, and the place where the key metrics will be read. It serves the builder first, then everyone the later stories serve.

#### How it's checked
The human demo: the Render service answering with stub data over TLS, MCP Inspector listing the three tools from the stub server, the gate running and failing on a deliberate break, the empty metrics report, and the spike result for database roles on Render.

#### Acceptance criteria
AC1. Stub MCP server lists the three tools
   Given the stub MCP server is started with the fixed stub client key in its environment
   When MCP Inspector connects over stdio using the committed JSON connection snippet
   Then it lists get_random_bytes, random_int and generate_token, each with its input fields, and the server's log lines are saved to `logs/server.log`
   Evidence: `make mcp-list` prints the three tool names and their inputs; the human also opens MCP Inspector (`npx @modelcontextprotocol/inspector`) with the snippet in `docs/mcp-client.json`; `head -1 logs/server.log`
AC2. A stub tool returns contract-shaped data
   Given the stub MCP server is connected
   When get_random_bytes is called with length 8 and encoding hex
   Then the result has a 16-character value and metadata with source, batch_id, batch_ids, bytes_consumed and reference
   Evidence: `make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'`
AC3. The deployed service answers over TLS
   Given the stub HTTP service is deployed on Render's free web service
   When GET /health is requested on the https URL
   Then the response is 200 with reporting, reportAgeSeconds, pool and upstream fields
   Evidence: `curl -s https://$RENDER_URL/health`
AC4. The deployed service refuses plain HTTP
   Given the deployed service
   When GET /health is requested on the http URL
   Then the request is refused or redirected to https
   Evidence: `curl -sI http://$RENDER_URL/health` shows a refusal or a 301/308 to https
AC5. Stub routes answer in the contract's shapes and roles
   Given the deployed stub service and the two fixed stub keys
   When POST /verify, GET /admin/usage and GET /admin/issuances/{reference} are called with the right key
   Then each returns its contract's success shape, and a call with no key returns 401 unauthorized
   Evidence: `curl -s -X POST -H "Authorization: Bearer $CLIENT_KEY" -d '{"owner":"user-42","value":"123456"}' https://$RENDER_URL/verify`
AC6. Migrations create all eight tables and are safe to run twice
   Given an empty Postgres 18 from Docker Compose and an empty Render Postgres
   When `make migrate` runs twice against each
   Then all of credentials, budget, batches, issuances, issuance_batches, tokens, redemption_outcomes and server_status exist, the budget row holds cap_kib 10240 and used_kib 0, the second run exits 0 and `make db-reset` returns to that empty migrated state
   Evidence: `make db-up && make migrate && make migrate && psql "$DATABASE_URL" -c '\dt' -c 'SELECT cap_kib, used_kib FROM budget'`
AC7. The database roles spike passes on Render
   Given a Render free Postgres 18 database
   When `make spike-roles` creates a role, grants it insert only on a scratch parent table and on a child table that references it, logs in as it and inserts without RETURNING
   Then the insert succeeds, an insert into a child table that references a parent the role cannot read succeeds, and a select as that role is denied
   Evidence: `make spike-roles DATABASE_URL=$RENDER_ADMIN_URL` prints PASS for create, grant, insert and denied-select
AC8. The regression gate passes and fails on a break
   Given a clean checkout with no network
   When `make gate` runs lint, the offline tests, the secret scan over full history, the import-direction check and a stub tool-call p95 check under 50 ms
   Then it exits 0, holds the repository to one source language, and exits non-zero after the p95 threshold is set to 0 ms or a module imports a transport
   Evidence: `make gate; echo $?` then repeat with `GATE_P95_MS=0 make gate; echo $?`
AC9. The gate runs on every push
   Given the committed GitHub Actions workflow and a push to the working branch
   When the push reaches GitHub
   Then a workflow run of `make gate` starts and finishes green
   Evidence: `gh run list --branch $BRANCH --limit 1` shows the run and its conclusion
AC10. The gate holds the matrix and fuzz harnesses
   Given the stub tools and routes are registered
   When `make gate` runs the authorization matrix over every registered tool and route with no key, a client key and an admin key, and a 1,000-input fuzz pass
   Then 0 calls succeed outside the allowed role and 0 inputs cause an unhandled exception or a 5xx
   Evidence: `make gate` output lists matrix cells and fuzz inputs run
AC11. The metrics report shows an empty panel for each key metric
   Given an empty database
   When `make report` runs
   Then it prints 9 panels named after the key metrics, each reading no data, and exits 0
   Evidence: `make report` shows: codes never issued per day, replies with an unbacked value, tokens redeemed before expiry, tool-call p95, server-caused errors, Qrypt requests worst 10 s, KiB per Qrypt request, Qrypt use against the cap, time to unavailable

#### Not in this story
- Real behaviour behind any tool, route or module interface: each is made real by the module story named in the coverage table, then wired by its wiring story
- Database roles, grants and guards beyond one connection role used by the stubs: picked up by S02
- Real key checking: the stub accepts two fixed test keys, one admin and one client; picked up by S05 and S06
