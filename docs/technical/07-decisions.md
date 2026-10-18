# Decisions and assumptions

## Decisions and assumptions

### Decisions that shape the build

| Decision | Why | Where it shows |
|---|---|---|
| Pool entropy in memory, serve each byte once | Meets the 64 KiB-per-request efficiency target and the rate limit; nothing sensitive on disk (CP-2) | `service/pool.py` |
| The audit row commits in the same transaction as the token hash, before the value returns | No value without a record (RL-6, FR-4.2) | `Issuance._audit` |
| Least privilege by database roles and guard triggers, not application code | A prompt or a bug in the application cannot read tokens or edit the audit | `s02_dbpolicy` |
| Redemption is one conditional `UPDATE` | Two simultaneous redemptions cannot both succeed (VR-4) | `Tokens.redeem` |
| The budget meter lives in the database and counts KiB reserved | Restarts and several processes cannot reset or exceed it (RL-4, VR-7) | `Meter.reserve` |
| The MCP server reports its state to the database every 5 s | The hosted service cannot see the MCP server's memory | `Status` |
| Issued values never reach the model; the agent substitutes them | Free providers may log or train on prompts (CP-5) | `agent/chat.py` |
| Rejection sampling for integers and codes, never a modulo | No bias (VR-2) | `Issuance.sample_int`, `_draw_otp` |
| Fail closed: no fallback entropy source, no value without a record | Provenance is the product (FR-6.1) | `Pool`, `Entropy source` |
| Only routes that return no entropy-derived value are hosted | Qrypt Terms 1.8(a) (GC-7) | `http_app.py` |
| Postgres with real roles, not SQLite | AZ-3 and VR-10 need database-enforced privileges (GC-5) | 04 |
| No Docker daemon in the build: per-worktree local databases | Environment | `docs/DEVIATIONS.md` 1 |

### Open questions of the architecture, as built

| # | Question | As built |
|---|---|---|
| 1 | Can the free Render database create roles and column grants? | `make spike-roles` passes on local Postgres 16; on Render it is unrun, with Neon as the fallback `[unverified]` |
| 2 | VR-8 for multi-draw calls | Option A: the "0 bytes removed" rule holds for a call's first draw; later failures discard what was drawn and write no row |
| 3 | Does a free model copy a placeholder reliably? | Not tried with a real model `[unverified]`; the agent has no fallback that appends the value itself |
| 4 | Metrics across the process boundary | Through `server_status.snapshot`, as designed |
| 5 | Meter counts KiB "requested" | A 429 retry inside one fetch reuses the reservation and is not counted again; a retry does not burn extra budget (`make two-process-budget` checks it against the mock's request log) |
| 6 | The reply scan is a pattern check | Option A, with the redemption outcome `unknown` as the second line (08) |

### Assumptions

| # | Assumption | Status |
|---|---|---|
| A-1 | A client starts the MCP server with its key in the server's environment | built |
| A-2 | Keys and database ids come from the operating system's random generator | built |
| A-3 | `generate_token` with an owner and no expiry uses 600 s, with a configurable maximum | built |
| A-4 | Lowering the cap below use is allowed and leaves the budget exhausted | built |
| A-5 | `/health` open; `/metrics` and `/admin/*` admin; `/verify` client or admin | built |
| A-6 | Expiry uses the database clock; tests use short expiries | built |
| A-7 | Two live tokens with the same owner and value are accepted at about 1 in 10^6 (six-digit codes) | built; redeeming tries each matching token |
| A-8 | Tool metadata adds `batch_ids` and `reference` | built |
| A-9 | `/verify` answers 404, 409, 410, 429 for refusals | built |
| A-11 | An owner is at most 256 characters | built (check constraint and validation) |
| A-13 | Bytes already in the pool may be served after the budget is spent | built; the pool serves them, only refills stop |
| A-14 | The MCP server reads its own secrets; the launcher supplies only the client key | built |
| A-15 | Old `server_status` rows are left and ignored | built |

### Where the build differs from the design documents

Full list with reasons: `docs/DEVIATIONS.md` (numbered 1 to 22). The ones a reviewer should know:

- Local database is Postgres 16 on a shared server, not Postgres 18 in Compose (1); the Postgres 18 path is unrun.
- Nothing ran on Render, GitHub Actions or live Qrypt (2, 3, 4, 18, 21).
- The secret scan falls back to a regex scanner when the gitleaks CLI is absent (5); CI installs gitleaks.
- The pool waits 1.8 s, not 2.0 s, for a refill (12).
- The startup check's 1 KiB is served after every refill batch so values name a 64 KiB batch (14).
- The MCP server's start waits at most 5 s for the Qrypt startup check, which may finish later in the background (16).
- The tables are owned by whoever ran the migration, not by `migrator` (9).

### Stubs

`docs/STUBS.md` lists every contract stub. On this build the module stubs are replaced and wired; what remains is the
provisional state described there (for example, panels fed from logs read "no data" until a run produces the logs).
