# STUBS.md — every contract stub S01 leaves behind

Stubs return contract-shaped synthetic data. Each is marked in code with `@stub("S..")` (`src/qrypt_entropy/stubs.py`),
and `tests/test_stubs_doc.py` fails if a stub is missing here or names a different story. When a module story replaces a
stub it deletes the decorator and this row's story column is then history; the wiring story connects the flow.

**Module story** = builds the real behaviour behind the interface, tested with the other modules stubbed.
**Wired by** = the wiring story that connects the module to its transport and removes the stub from that flow.
Each module lives in one file, so module stories do not collide.

| Module (file) | Stub | Replaced by | Wired by |
|---|---|---|---|
| `qrypt_entropy.service.meter` (`service/meter.py`) | `get_usage` | S24 | S34 |
| `qrypt_entropy.service.meter` | `set_budget` | S24 | S34 |
| `qrypt_entropy.upstream.source` (`upstream/source.py`) | _none: `fetch` and `request_stats` are real since S08; S09 adds the 429 backoff and the 30-per-10-s limiter (one fetch can take about 2 min of backoff: callers wait on the Pool, never on fetch); mock in `tests/mock_qrypt.py`_ | S08 | S20 |
| `qrypt_entropy.service.pool` (`service/pool.py`) | `take` (module-level; the real `Pool` class exists since S10) | S20 (S11 failure paths) | S20 |
| `qrypt_entropy.service.pool` | `status` (module-level) | S35 | S35 |
| `qrypt_entropy.service.pool` | `start` | S12 | S20 |
| `qrypt_entropy.service.pool` | `stop` | S12 | S20 |
| `qrypt_entropy.service.issuance` | `random_int` | S14 | S20 |
| `qrypt_entropy.service.issuance` | `generate_token` | S15 | S20 |
| `qrypt_entropy.service.issuance` | `get_issuance` | S23 | S33 |
| `qrypt_entropy.service.tokens` | `redeem` | S21 (S22 rate limit) | S33 |
| `qrypt_entropy.service.status` (`service/status.py`) | `get_health` | S29 | S35 |
| `qrypt_entropy.service.status` | `get_metrics` | S30 | S35 |
| `qrypt_entropy.service.status` | `report_once` | S28 | S35 |
| `qrypt_entropy.agent.chat` (`agent/chat.py`) | `chat_turn` | S25 (S26, S27) | S32 |

Not decorated but also provisional:

| Item | State | Replaced by |
|---|---|---|
| `service/report.py` panels fed from logs or server reports (5 of 9) | always "no data" | S19 (logs), S28 (reports); table-fed panels refined by S18 |
| Database roles, grants, guard triggers (append-only, meter-only-rises, one-way used flag) | real since S02: migration `s02_dbpolicy` (roles have no password until `scripts/set_role_passwords.py` runs; tables stay owned by whoever runs the migration, see DEVIATIONS 9) | done |
| Test keys `stub-client-key`, `stub-admin-key` | constants in `service/access.py`; accepted only when `tests/conftest.py` has inserted credential rows for them (S05 made Access real: HMAC-hashed keys in `credentials`, revocation, `createClient` / `revokeClient`). The MCP start check on connect and every call is still S06 | S06 |
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
