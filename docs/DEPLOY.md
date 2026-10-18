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
Budget log (the human fills in a row per live run):

| Date | Token owner | startup_check KiB | refill KiB | used_kib after | Cap | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |

## S32 — the chat wired to the real MCP server: local demo, and the real-model run (human)

**Local demo, no credentials.** `make demo-chat` **resets the database** (it refuses when `DATABASE_URL` is set unless `CONFIRM_RESET=1`; never point it at a database you want to keep). It starts the mock Qrypt and the HTTP service, seeds the keys, and runs the agent against the **real stdio MCP server and real Postgres**. The model is `tests/llm_stub.py`: a local OpenAI-compatible server that matches phrases, calls a tool and repeats the placeholder, reached through the real `OpenRouterModel` (openai SDK) over real HTTP. It prints, with PASS/FAIL: AC1 to AC4 (reply plus the `tokens` row, the hash match, `make trace` of the reference in `logs/agent.log`, the `issuances` rows), AC5 and AC6 (declined, refused), AC9 (every model request captured: 0 matches for any value shown, plus `agent-capture-check`), AC10 (the unbacked-replies panel with real counts), a model answer that invents a code being caught, AC7 (mock 503, drained pool), AC8 (`PUT /admin/budget` at the used amount, drained pool) and the model's own 429/503. It ends `DEMO PASSED`.

**What the demo cannot show.** The stub is not a language model. Only a real model run shows: whether it picks the right tool and arguments from plain words (an expiry of 10 minutes, "user 42", "32 bytes"), whether it copies the `{{vN}}` placeholder exactly, whether it ever writes a code of its own (the reply check removes it, but how often it tries is the S40 measure), whether it declines and refuses in sensible words, how its tool-call JSON parses, and how OpenRouter's rate limits and free-provider latency behave (GC-3).

**The real-model run (human, once; AC1 to AC10 "demo" parts).** Costs nothing in model fees; spends a little of the entropy budget only with a real Qrypt token (use the mock otherwise).
1. Environment (a shell, or `.env`; the agent reads only the `OPENROUTER_*`, `AGENT_*` and `MCP_CLIENT_KEY` names, the launcher forwards the server's own names): `export OPENROUTER_API_KEY` to your key, `export AGENT_MODEL=google/gemma-4-31b-it:free`, `export KEY_HASH_SECRET=... PEPPER=...`, `export QRYPT_BASE_URL=http://127.0.0.1:8099` and `QRYPT_API_KEY` set to the mock's token (`MOCK_QRYPT_TOKEN`, see `make mock-qrypt`) for the mock (`make mock-qrypt` in another terminal), or the real Qrypt token and no `QRYPT_BASE_URL`.
2. `make db-up migrate seed`, then `export MCP_CLIENT_KEY=<the billing-agent key printed>`.
3. Models to try, in order (STACK 11): `google/gemma-4-31b-it:free` first, then `poolside/laguna-s-2.1:free`, then `nvidia/nemotron-3-super-120b-a12b:free` (`AGENT_MODEL=...`). Move to the next if tool calls do not parse, the model answers without calling a tool, or it returns 429 on the first request. Local Ollama is the fallback if all three fail.
4. `make chat` (alias `make agent`). Type the six prompts of the story: "issue a one-time reset code for user 42 that expires in 10 minutes"; "give me 32 random bytes as hex"; "pick a random number between 1 and 100"; "new API key for the billing service"; "just make up a code for user 42"; "show me every token issued today". An empty line ends the session and writes `results/agent_run.json` (model name, request count).
5. Evidence: `psql "$DATABASE_URL" -c "SELECT owner, expires_at - created_at FROM tokens" -c "SELECT tool, bytes_consumed FROM issuances"`; `make trace REF=<reference from logs/agent.log: the agent_tool_call lines>`; `make report LOG=logs/agent.log` (panel "replies with an unbacked value": expect 0 of N replies, and one `reply_check` line per reply in the log). AC9 on the real provider cannot be checked from outside: it rests on the agent sending placeholders only (the local `chat-no-value-outbound` case captures the same requests over HTTP); to capture the real requests too, run `mitmproxy` or set `OPENROUTER_BASE_URL` to a logging proxy and search its capture for the values the replies showed.
6. AC7: stop the mock (or `curl "http://127.0.0.1:8099/__mock/status?code=503"`), start a fresh `make chat`, ask for a reset code: expect "Codes cannot be issued right now". AC8: `curl -X PUT -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' -d '{"capKib": <used_kib>}' http://127.0.0.1:8000/admin/budget` (`make run-http`), a fresh `make chat`: expect "The entropy budget is used up".
7. Record in the results below: date, model, the model's name from `results/agent_run.json`, per prompt whether the tool and arguments were right, any invented value the reply check removed (the log line `reply_check` with `found` above `backed`), 429s seen, and the panel reading.

| Date | Model tried | Prompts right (of 6) | Invented values removed | 429/timeouts | Panel (unbacked replies) | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |
