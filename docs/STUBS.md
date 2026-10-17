# STUBS.md — every contract stub S01 leaves behind

Stubs return contract-shaped synthetic data. Each is marked in code with `@stub("S..")` (`src/qrypt_entropy/stubs.py`),
and `tests/test_stubs_doc.py` fails if a stub is missing here or names a different story. When a module story replaces a
stub it deletes the decorator and this row's story column is then history; the wiring story connects the flow.

**Module story** = builds the real behaviour behind the interface, tested with the other modules stubbed.
**Wired by** = the wiring story that connects the module to its transport and removes the stub from that flow.
Each module lives in one file, so module stories do not collide.

| Module (file) | Stub | Replaced by | Wired by |
|---|---|---|---|
| `qrypt_entropy.upstream.source` (`upstream/source.py`) | _none: `fetch` and `request_stats` are real since S08; S09 adds the 429 backoff and the 30-per-10-s limiter (one fetch can take about 2 min of backoff: callers wait on the Pool, never on fetch); mock in `tests/mock_qrypt.py`_ | S08 | S20 |
| `qrypt_entropy.service.pool` (`service/pool.py`) | `take` (module-level; the real `Pool` class exists since S10) | S20 (S11 failure paths) | S20 |
| `qrypt_entropy.service.pool` | `status` (module-level) | S35 | S35 |
| `qrypt_entropy.service.pool` | `start` | S12 | S20 |
| `qrypt_entropy.service.pool` | `stop` | S12 | S20 |
| `qrypt_entropy.service.issuance` | `random_int` | S14 | S20 |
| `qrypt_entropy.service.issuance` | `generate_token` | S15 | S20 |
| `qrypt_entropy.service.issuance` | `get_issuance` | S23 | S33 |
| `qrypt_entropy.service.tokens` | `redeem` | S21 (S22 rate limit) | S33 |
| `qrypt_entropy.service.issuance` | `call_stats` | S28 | S35 |
| `qrypt_entropy.service.status` (`service/status.py`) | `get_health` | S29 | S35 |
| `qrypt_entropy.service.status` | `get_metrics` | S30 | S35 |
| `qrypt_entropy.service.status` | `report_once` | S28 | S35 |
| `qrypt_entropy.agent.chat` (`agent/chat.py`) | `chat_turn` | S25 (S26, S27) | S32 |

Not decorated but also provisional:

| Item | State | Replaced by |
|---|---|---|
| `service/report.py` panels fed from logs or server reports (5 of 9) | always "no data" | S19 (logs), S28 (reports); table-fed panels real since S18 (S33 and S20 replace the writers of their rows) |
| `service/report.py` panels fed from logs or snapshots (5 of 9) | real since S19 (`service/report_logs.py`; `make report LOG= SNAPSHOTS=`; `make sample-metrics` writes snapshots). They read "no data" until the writers emit the **pinned names** below | S25, S27, S28, S30 emit the inputs; table-fed panels refined by S18 |
| Database roles, grants, guard triggers (append-only, meter-only-rises, one-way used flag) | real since S02: migration `s02_dbpolicy` (roles have no password until `scripts/set_role_passwords.py` runs; tables stay owned by whoever runs the migration, see DEVIATIONS 9) | done |
| Test keys `stub-client-key`, `stub-admin-key` | constants in `service/access.py`; accepted only when `tests/conftest.py` has inserted credential rows for them (S05 made Access real: HMAC-hashed keys in `credentials`, revocation, `createClient` / `revokeClient`). S06 (done): the MCP server checks the key at start (a missing, unknown, revoked or admin key exits 1 with the connect_refused log line on stderr, no tool listed) and on every call; `make mcp-list` reads `CLIENT_KEY` (or `MCP_CLIENT_KEY`) and no longer defaults to the stub key | done |
| `service/pool.py` module-level `take`/`status` | still stubs: the real `Pool` class (S10: take, status, stop, background refill) is built and tested but not yet instantiated by the server, so issuance keeps its synthetic bytes | S20 wires a running `Pool` in and removes them |
| `transport/http_app.py` and `transport/mcp_server.py` | real transports; their routes and tools call the stub modules above | each wiring story as listed |
| `storage/db.py` | real since S04: shared pool per URL, checked before use (reconnects after a restart), `connection()` maps an unreachable database to `TemporarilyUnavailable` (503 / tool error, no value). Modules should use `db.connection`; `meter` already does | done |

S07 (done): `meter.reserve` and `meter.mark_batch` are real and use the database. The callers (Pool, Entropy source) are
still stubs, so nothing in the running flows calls them until S10 and S20. The 80% and 100% "once" guard for refusals is
kept per process (a reserve that crosses a threshold logs it exactly once across all processes).

S11 (done): `Pool.take` waits up to 2 s for a refill and then raises `PoolStarved` (code = reason: `temporarily_unavailable`, `budget_exhausted` or `upstream_misconfigured`); a failed refill is retried after a growing pause (0.5 s doubling to 10 s, with jitter) instead of at once; the `pool_cannot_cover` and `pool_error_returned` events (OB-8) are logged. `RateLimited` was added to `errors.py` with the same definition S09 uses. The module-level `take`/`status` stubs are unchanged (S20 and S35).

S13 (done): `Issuance.get_random_bytes` and `Issuance.call_stats` are real: validation, the audit transaction (`issuances`
and `issuance_batches`, committed before the value returns), a structured `tool_call` log line with `duration_ms`, and
call counters (calls by tool, errors by code, duration percentiles) kept per process. `call_stats` counts only
`get_random_bytes` until S14 and S15 land; S28 only has to read it. Because audit rows have foreign keys, the stub
pool batch must exist in `batches`: `qrypt_entropy.stubs.seed_stub_batch` inserts it (the test suite does it once per
session). `make mcp-call` runs `scripts/seed_stubs.py`, which also inserts the two stub credentials with real hashed keys
and refuses a non-local database; it needs `KEY_HASH_SECRET` in the environment or `.env`. S20 removes both with the stubs.
S17 (done): `scripts/seed.py` (`make seed`, `make seed-list-names`) creates the admin, `billing-agent` and `support-agent` through
`Access.create_credential` and three seed tokens through `Tokens.storeSeedToken`, with no entropy source. It needs
`KEY_HASH_SECRET` and `TOKEN_PEPPER` in the environment. It owns no stubs, so the stub tables above are unchanged.
S21 (done): `tokens.redeem` is real (one conditional UPDATE on `used_at` per candidate, outcome row in the same transaction); `POST /verify` answers 200/404/409/410. S22 adds the failed-attempt limit and the 429 `rate_limited` outcome. S33 wires the redeemer flow end to end.
**Pinned names the S19 report reads (writers must emit exactly these):**
- Log `tool_call` with `duration_ms` (real in `mcp_server.py`); `pool_error_returned` with `waited_s` (real since S11, `pool.py`; `pool_cannot_cover` is not read).
- Agent log `reply_check` with `found` and `backed` (counts, never values), one per reply: S25 / S27 emit it.
- `/metrics` series (S30): `qrypt_errors_total{code}`, `qrypt_calls_total{tool}`, `qrypt_upstream_requests_last_10s`, `qrypt_process_start_time_seconds` (the snapshot's `startedAt`).

S14 (done): `Issuance.random_int` is real. `sample_int(span, draw)` is rejection sampling (draw ceil(bits/8) bytes, mask to the bits the span needs, redraw when out of range; never a modulo, VR-2). A call may draw several times: every byte drawn, kept or rejected, is in `bytes_consumed` and in its `issuance_batches` rows (CT-1). Wording assumed for the open VR-8 question (ARCHITECTURE open question 2, option A): the "0 bytes removed" rule holds for a call's first draw; if a later draw fails, bytes already drawn are discarded, never given to anyone else, and no issuance row is written (the call returns the error). A call with min = max still draws one byte so it has an audit slice. S15 reuses `sample_int` for `otp_6`. The tool is still reached through the stub pool (`pool.take`) and `Access` stubs until S20 and S06.
S12 (done): `Pool.start` is real: it resets `_stopped`, `_error` and `lastSuccessAt`, makes one 1 KiB request with purpose `startup_check` (the bytes join the pool), then begins the refill loop. A failed check never raises: it is logged as `pool_startup_check_failed` (a 401 names the invalid or expired Qrypt token), and a 401 does not start the refill loop until a `take` asks. `Pool.status()` upstream_status: `ok`; `misconfigured` (latest failure was a 401); `failing` (latest fetch failed and the pool is empty or a call is waiting); `degraded` (latest fetch failed but the pool still holds bytes); a successful fetch returns it to `ok` with `lastSuccessAt` set. The module-level `start`/`stop` stubs were removed (nothing called them); S20 constructs a `Pool` and calls `await pool.start()`.
S12 (done): `Pool.start` is real: it resets `_stopped`, `_error` and `lastSuccessAt`, makes one 1 KiB request with purpose `startup_check` (the bytes join the pool), then begins the refill loop. A failed check never raises: it is logged as `pool_startup_check_failed` (a 401 names the invalid or expired Qrypt token), and a 401 does not start the refill loop until a `take` asks, and the refill loop stops after a 401 (one attempt per take, so a bad token cannot drain the budget). `Pool.status()` upstream_status: `ok`; `misconfigured` (latest failure was a 401); `failing` (latest fetch failed and the pool is empty or a call is waiting); `degraded` (latest fetch failed but the pool still holds bytes); a successful fetch returns it to `ok` with `lastSuccessAt` set. The module-level `start`/`stop` stubs were removed (nothing called them); S20 constructs a `Pool` and calls `await pool.start()`.

S24 (done): `Meter.get_usage` and `Meter.set_budget` are real, and `GET /admin/usage` / `PUT /admin/budget` answer from them; the two stub rows are gone. `perClient` lists the clients that have at least one issuance (summed from `issuances` alone, so it can be re-derived from the audit rows, CH-3), ordered by name; a client with no calls is absent. `set_budget` writes only `cap_kib` and `updated_at` (the `http_service` grant) and accepts 0 to 2^63-1; it re-arms the per-process 100% alert flag of the process it runs in, so a MCP server process in another process keeps its own flag until it restarts. The routes read real rows now: the usage numbers are real until S20 makes the issuance writers real; before that only S13's `get_random_bytes` writes audit rows. The matrix and fuzz gates call `PUT /admin/budget` on the gate database, so `tests/conftest.py` resets the cap to 10240 at the start and end of the session.
