# STUBS.md — every contract stub S01 leaves behind

Stubs return contract-shaped synthetic data. Each is marked in code with `@stub("S..")` (`src/qrypt_entropy/stubs.py`),
and `tests/test_stubs_doc.py` fails if a stub is missing here or names a different story. When a module story replaces a
stub it deletes the decorator and this row's story column is then history; the wiring story connects the flow.

**Module story** = builds the real behaviour behind the interface, tested with the other modules stubbed.
**Wired by** = the wiring story that connects the module to its transport and removes the stub from that flow.
Each module lives in one file, so module stories do not collide.

| Module (file) | Stub | Replaced by | Wired by |
|---|---|---|---|
| `qrypt_entropy.service.access` (`service/access.py`) | `authenticate` | S05 (S06 for connect) | S20 |
| `qrypt_entropy.service.access` | `hash_key` | S05 | S20 |
| `qrypt_entropy.service.access` | `create_credential` | S05 | S20 |
| `qrypt_entropy.service.access` | `create_client` | S24 | S34 |
| `qrypt_entropy.service.access` | `revoke_client` | S24 | S34 |
| `qrypt_entropy.service.meter` (`service/meter.py`) | `get_usage` | S24 | S34 |
| `qrypt_entropy.service.meter` | `set_budget` | S24 | S34 |
| `qrypt_entropy.upstream.source` (`upstream/source.py`) | _none: `fetch` and `request_stats` are real since S08 (no retry or rate limit until S09); mock in `tests/mock_qrypt.py`_ | S08 | S20 |
| `qrypt_entropy.service.pool` (`service/pool.py`) | `take` | S10 (S11 failure paths) | S20 |
| `qrypt_entropy.service.pool` | `status` | S10 | S35 |
| `qrypt_entropy.service.pool` | `start` | S12 | S20 |
| `qrypt_entropy.service.pool` | `stop` | S12 | S20 |
| `qrypt_entropy.service.issuance` (`service/issuance.py`) | `get_random_bytes` | S13 | S20 |
| `qrypt_entropy.service.issuance` | `random_int` | S14 | S20 |
| `qrypt_entropy.service.issuance` | `generate_token` | S15 | S20 |
| `qrypt_entropy.service.issuance` | `get_issuance` | S23 | S33 |
| `qrypt_entropy.service.issuance` | `call_stats` | S28 | S35 |
| `qrypt_entropy.service.tokens` | `redeem` | S21 (S22 rate limit) | S33 |
| `qrypt_entropy.service.status` (`service/status.py`) | `get_health` | S29 | S35 |
| `qrypt_entropy.service.status` | `get_metrics` | S30 | S35 |
| `qrypt_entropy.service.status` | `report_once` | S28 | S35 |
| `qrypt_entropy.agent.chat` (`agent/chat.py`) | `chat_turn` | S25 (S26, S27) | S32 |

Not decorated but also provisional:

| Item | State | Replaced by |
|---|---|---|
| `service/report.py` panels fed from logs or server reports (5 of 9) | always "no data" | S19 (logs), S28 (reports); table-fed panels refined by S18 |
| Database roles, grants, guard triggers (append-only, meter-only-rises, one-way used flag) | not created: migration `0001` makes tables, checks, foreign keys and indexes only | S02 |
| Stub keys `stub-client-key`, `stub-admin-key` | fixed test keys inside `service/access.py` | S05, S06 |
| `transport/http_app.py` and `transport/mcp_server.py` | real transports; their routes and tools call the stub modules above | each wiring story as listed |
| `storage/db.py` | pool helper, unused by the stubs | S04 (restart recovery), then modules |

S07 (done): `meter.reserve` and `meter.mark_batch` are real and use the database. The callers (Pool, Entropy source) are
still stubs, so nothing in the running flows calls them until S10 and S20. The 80% and 100% "once" guard for refusals is
kept per process (a reserve that crosses a threshold logs it exactly once across all processes).
