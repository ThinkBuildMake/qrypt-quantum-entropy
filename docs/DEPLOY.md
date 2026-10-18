# DEPLOY.md — S01 stub service on Render, and the checks that need Render

**The deployment guide is `docs/DEPLOYMENT_GUIDE.md`** (steps, limits, backup, budget log, first-deploy checklist); where this file differs, the guide is current. This file keeps the S01 check commands and the S20 live-run procedure.

Nothing here has been run: S01 was built without a Render account (see `docs/DEVIATIONS.md`).

1. In Render choose New, Blueprint, point it at this repository: `render.yaml` creates a free web service
   (`qrypt-entropy`) and a free Postgres 18 (`qrypt-db`) and wires `DATABASE_URL`. Do not create the production database
   before 2026-10-20 (`STACK.md` 19); this one is the spike/demo database.
2. The start command runs `alembic upgrade head` (safe to run twice), then uvicorn.
3. Checks (AC3, AC4, AC5, AC6, AC7). `RENDER_URL` is the host name without scheme; `CLIENT_KEY` and `ADMIN_KEY` are the keys `make seed` printed against that database (the stub keys no longer exist):
   ```
   curl -s https://$RENDER_URL/health
   curl -sI http://$RENDER_URL/health                       # expect a 301/308 to https, or a refusal
   curl -s -X POST -H "Authorization: Bearer $CLIENT_KEY" -d '{"owner":"user-42","value":"123456"}' \
        -H 'Content-Type: application/json' https://$RENDER_URL/verify
   curl -s -H "Authorization: Bearer $ADMIN_KEY" https://$RENDER_URL/admin/usage
   curl -s https://$RENDER_URL/admin/usage                  # expect 401 unauthorized
   make migrate migrate DATABASE_URL=$RENDER_EXTERNAL_URL   # twice; then psql -c '\dt' -c 'SELECT cap_kib, used_kib FROM budget'
   make spike-roles DATABASE_URL=$RENDER_ADMIN_URL          # expect PASS create, grant, insert, denied-select
   ```
   The external connection string needs `?sslmode=require` if it does not already carry it.

## S20 — the issue flow: demo, and the live Qrypt run (AC9)

**Local demo, no token needed.** `make demo-issue` **resets the database** (drops and re-migrates it; with Docker it also drops the Compose volume). It refuses to run when `DATABASE_URL` is set (an external database) unless you pass `CONFIRM_RESET=1`; never point it at a database you want to keep. It resets the local database, starts the mock Qrypt (`tests/mock_qrypt.py`) and the
HTTP service, seeds the keys, drives the MCP server over stdio and prints each step with its evidence query and PASS/FAIL, then the
nine report panels. It ends with `DEMO PASSED` or exits 1. To drive it by hand with MCP Inspector instead: `make mock-qrypt`
(terminal 1; `MOCK_QRYPT_TOKEN` or the default `mock-token-0123456789`), then `make db-up migrate seed`, put the billing-agent key in
`docs/mcp-client.json` (replace every `REPLACE_WITH_...` value, keep `QRYPT_BASE_URL` at `http://127.0.0.1:8099` and `QRYPT_API_KEY`
at the mock's token) and connect. `GET http://127.0.0.1:8099/__mock/status?code=503` makes the mock fail (AC10); `code=200` restores it.

**AC9, the live run (human, once; the allotment is finite).**
1. A database whose meter is at 0: `psql "$DATABASE_URL" -c 'SELECT used_kib, cap_kib FROM budget'` shows `0`. If not, use a fresh database (`make db-reset`) so the reading is the run's own.
2. `export QRYPT_API_KEY=<the real Entropy-service token>`; leave `QRYPT_BASE_URL` unset (default `https://api-eus.qrypt.com`); export `DATABASE_URL`, `KEY_HASH_SECRET` and `PEPPER`; then `make migrate seed` and note the billing-agent key.
3. In `docs/mcp-client.json` set `MCP_CLIENT_KEY` to that key, `QRYPT_API_KEY` to the real token, delete the `QRYPT_BASE_URL` line, and connect MCP Inspector. The server's startup check makes the first Qrypt request.
4. Call `get_random_bytes` with `length` 16 and `encoding` hex, once. Disconnect.
5. Read the evidence and write the reading in the budget log (`docs/DEPLOYMENT_GUIDE.md` section 8):
   ```
   psql "$DATABASE_URL" -c 'SELECT purpose, kib, outcome FROM batches ORDER BY requested_at' -c 'SELECT used_kib FROM budget'
   ```
   Expected: one `startup_check` of 1 KiB and one `refill` of 64 KiB or more, both `received`, and `used_kib` 1,024 or less (65 with the defaults).
   If the startup check shows `failed`, read `logs/server.log` for `pool_startup_check_failed`: a 401 names an invalid or expired token.

Budget log: the single running log, with a total across databases, is in `docs/DEPLOYMENT_GUIDE.md` section 8. Write the reading there.
