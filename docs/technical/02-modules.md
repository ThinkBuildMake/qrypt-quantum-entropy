# Modules

## Modules

The application is split by the data each part owns. A module changes another module's table only through that module's
interface. Each module is one file. "Owns" means the only code that writes the table.

| Module | File | Owns (writes) | Runs in |
|---|---|---|---|
| Access | `service/access.py` | `credentials` | MCP server (read), HTTP service |
| Meter | `service/meter.py` | `budget`, `batches` | MCP server (reserve, mark), HTTP service (cap) |
| Entropy source | `upstream/source.py` | nothing | MCP server |
| Pool | `service/pool.py` | nothing (memory only) | MCP server |
| Issuance | `service/issuance.py` | `issuances`, `issuance_batches` | MCP server (write), HTTP service (trace, read) |
| Tokens | `service/tokens.py` | `tokens`, `redemption_outcomes` | MCP server (insert), HTTP service (redeem) |
| Status | `service/status.py` | `server_status` | MCP server (reporter), HTTP service (readers) |
| Agent | `agent/` | nothing; no database | agent process |
| Storage | `storage/db.py` | nothing | all |

Shared code: `config.py` (settings and profiles), `errors.py` (the stable error codes), `logs.py` (JSON logs and the
secret filter), `models.py` (shared types).

### Access

Decides who may call what and creates, checks and revokes keys.

- A client or admin key is 32 bytes from the operating system's random generator (`secrets.token_urlsafe`), never from
  Qrypt, because the HTTP service must hold no entropy (GC-7). The key is returned once and stored only as
  HMAC-SHA-256 under `KEY_HASH_SECRET` (`hash_key`).
- `authenticate(key, allowed_roles)` returns a `Principal(client_id, role, name)`, or raises `unauthorized` (missing,
  unknown or revoked, with one message for all three) or `forbidden` (valid key, wrong role).
- `create_credential(role, name)`, `create_client(name)`, `revoke_client(client_id)`. Revoking twice keeps the first
  `revoked_at`. An admin credential cannot be revoked through the API.
- Has its own connection pool; an unreachable database becomes `temporarily_unavailable`.

### Meter

Keeps the entropy budget (default 10,240 KiB) and a record of every upstream request.

- `reserve(wanted_kib, purpose, source)` takes the `budget` row lock (`SELECT ... FOR UPDATE`), grants
  `min(wanted, cap - used)`, adds it to `used_kib` and inserts a `batches` row, all in one transaction. With nothing
  left it raises `budget_exhausted`. Because the count is in the database, restarts and several server processes cannot
  reset or exceed it.
- `mark_batch(batch_id, outcome)` sets `received` or `failed` once (the first mark stands) and never changes `used_kib`.
- `settle_stale()` marks batches a killed process left `requested` for more than 600 s as `failed`.
- `get_usage()` returns cap, used and bytes consumed per client (summed from `issuances`). `set_budget(cap_kib)` writes
  the cap; lowering it below use is accepted and leaves the budget exhausted (A-4).
- Logs `budget_alert` once when use crosses 80% and once at exhaustion (per process, per cap episode).
- The meter counts KiB reserved, which is KiB requested from Qrypt. A 429 retry inside one fetch reuses its reservation,
  so it is not counted again (see 07, open question 5).

### Entropy source

The swappable upstream client. `Source` is a protocol (`label`, `fetch(size_kib)`, `request_stats()`); `QryptSource`
is the real one and `TestSource` is a second, deterministic source labelled `test_source`.

- `GET {QRYPT_BASE_URL}/api/v1/quantum-entropy?size=<KiB>` with `Authorization: Bearer <token>`; size is 1 to 512 and
  anything else is rejected before any request (DE-2). The reply's base64 blocks are joined and must be exactly
  `size * 1024` bytes or the fetch fails as a malformed reply.
- A limiter allows at most 30 requests in any 10 s window, counting a request until it comes back, retries included
  (DE-1).
- On 429 the fetch waits 0.5 s, then doubles each time up to 60 s, each wait randomised to 50 to 100% of nominal, for up to
  8 retries, then raises `RateLimited`. One fetch can therefore take about two minutes; callers wait on the Pool, never
  on the fetch.
- Status mapping: 401 `upstream_misconfigured`; 403 `budget_exhausted` (allotment used, `AllotmentUsed`); other non-200,
  timeouts and connection errors `temporarily_unavailable`. No other source is ever tried on failure (FR-6.1).
- The Qrypt token is never logged.

### Pool

Holds fetched entropy in memory and hands each byte out once.

- Marks: low 32 KiB, high 128 KiB (settings). A refill asks for 96 KiB (so the KiB served per request can reach 64, PF-3), less when the pool has less headroom below the
  high mark. At most one refill runs at a time. Each start spends about 97 KiB of budget (1 KiB check plus the 96 KiB first prefetch). The
  pool logs `pool_marks_too_close` at start when high minus low is under the refill size.
- `take(n)` removes `n` bytes in one step, or none. It returns the bytes and their `BatchSlice` list (batch id, byte
  offset, count). If the pool cannot cover `n`, it waits up to 1.8 s for a refill, then raises `PoolStarved` whose code
  is the reason (`temporarily_unavailable`, `budget_exhausted` or `upstream_misconfigured`), so the error is out in
  under 2 s (RL-1).
- A refill that fails is still charged to the budget (a failed batch is never refunded), so repeated failure is
  bounded: retries pause 0.5 s doubling to 10 s with jitter; after 3 failures in a row the pool tries at most once per
  60 s and only for a take that is waiting; a 401 or 403 stops refilling and is retried at most every 300 s, and only
  when a take asks (`FATAL_RETRY_S`, `FAILURES_BEFORE_HARD_BACKOFF`, `HARD_BACKOFF_S`). A refused reservation (cap
  reached) is free and does not count; a take that finds one retries once at once, so a raised cap is used straight
  away. After `shutdown()` a take raises `PoolStarved` and no new pool is built.
- `start()` runs a 1 KiB startup check (DE-5); a failure is logged, not raised. Its bytes join the pool but are served
  after every refill batch, so a client sees a refill batch id rather than the 1 KiB check.
- `status()` is `ok`, `degraded` (a refill failed but the pool still serves), `failing` (empty or a call is waiting) or
  `misconfigured` (401).
- Pooled bytes live in this process's memory only (CP-2) and are discarded at stop or restart. KiB reserved for them are
  not refunded.
- The reservation call is blocking database work, so it runs on a small thread pool off the event loop.

### Issuance

The three tools and the audit record.

- `get_random_bytes(principal, length, encoding)`: length 1 to `PER_CALL_MAX_BYTES` (1024), encoding `hex` or `base64`.
- `random_int(principal, min, max)`: both within +/- (2^53 - 1), `min <= max`. Rejection sampling (`sample_int`): draw
  enough bytes for the span, mask to the bits it needs, redraw when out of range; never a modulo (VR-2). At least one
  byte is always drawn. Every byte drawn, kept or rejected, counts in `bytes_consumed` and in the call's audit slices.
- `generate_token(principal, kind, owner, expiry_seconds)`: `api_key` is 32 bytes as 64 hex characters; `otp_6` is a
  six-digit string by rejection over 3-byte draws (values below 16,000,000, then modulo 10^6, which is exact because
  16,000,000 is a multiple of 10^6); `uuid_v4` is 16 bytes with the version and variant bits set. `expiry_seconds`
  needs an `owner` and is 1 to `TOKEN_EXPIRY_MAX_SECONDS` (86,400); with an owner and no expiry the default is 600 s.
  With an owner the token hash is stored in the same transaction as the audit row; without one the value is returned
  and nothing about it is stored (it cannot be redeemed).
- The audit step (`_audit`) is one transaction that inserts `issuances`, `issuance_batches` and, for a redeemable token,
  `tokens`, and commits before the value is returned. If it fails, no value is returned (RL-6). A multi-draw call that
  fails on a later draw discards the bytes already drawn and writes no row (VR-8 as reworded, 07 open question 2).
- `get_issuance(reference)` returns provenance: who, which tool, bytes, the batches and byte ranges, and a token summary
  (owner, kind, expiry, used time, seed flag). Never a value or a hash.
- `call_stats()` keeps per-process counters (calls by tool, errors by code, duration p50, p95, p99 over the last 10,000
  calls) for the status report.

### Tokens

Redeemable tokens and redemption.

- `hash_token(owner, value)` is HMAC-SHA-256 under `PEPPER` of `owner + NUL + value`, so equal values of different owners
  differ. `hashes_equal` is the one constant-time comparison (`hmac.compare_digest`).
- `store_token` inserts on the caller's transaction (used by Issuance); `store_seed_token` is for the seed script.
- `redeem(owner, value)` takes a per-owner transaction advisory lock (waiting at most `RATE_LIMIT_LOCK_TIMEOUT_MS`, then
  `temporarily_unavailable`), counts the owner's failed outcomes in the window, and at `RATE_LIMIT_MAX_FAILURES` (5 in
  600 s) records `rate_limited` and looks at nothing else. Otherwise it flips `used_at` with one conditional `UPDATE ...
  WHERE used_at IS NULL AND expires_at > clock_timestamp()`, so two simultaneous redemptions give one `redeemed` (VR-4).
  It records exactly one `redemption_outcomes` row in the same transaction. Outcomes: `redeemed`, `already_used`,
  `expired`, `unknown`, `rate_limited`.
- Times use the database clock.

### Status

Lets the hosted service say something about a server it cannot see into.

- `StatusReporter` (MCP server) writes one `server_status` row per process every 5 s: pool level and marks, upstream
  status, last success, and a JSON snapshot (start time, Qrypt requests in the last 10 s, calls by tool, errors by code,
  duration percentiles). Counts and words only, never a value or entropy (OB-9).
- `get_health()` returns the newest report; one older than 15 s, or none, reads as `reporting: false`.
- `get_metrics()` renders Prometheus text from the newest report and the budget; the live series are omitted when the
  server is not reporting.

### Agent

The Secure Token Agent's chat loop (`agent/chat.py`). It holds no database connection and reads only its own settings
(`agent/settings.py`).

- The model is given tool specs from the MCP server (`mcp_tools.py` starts the server over stdio with only the client key
  and `PATH` in its environment) and sees tool results as a placeholder such as `{{v1}}` plus the metadata. The agent
  keeps the real value in memory and substitutes it into the reply itself (CP-5). Held values the user types back are
  swapped for their placeholders before any text goes to the model (values shorter than 6 characters are not).
- A turn allows at most 6 model requests; the last carries no tools. Every request is counted and `finish()` writes the
  count, the model name and the free-tier limit to `results/agent_run.json` (GC-3).
- Tool errors that mean nothing can be issued (`budget_exhausted`, `temporarily_unavailable`,
  `upstream_misconfigured`, `database_unavailable`) are answered in the agent's own sentence, never the model's.
  Model failures (429, timeout, other) get fixed plain sentences (`guard.py`).
- The reply check (`reply_check.py`) removes any value-shaped string the tools did not return in this conversation and
  logs `reply_check` with counts. It is a heuristic; see 08.
- `scripted.py` is the scripted model used by tests and `make demo-chat`; `openrouter.py` is the real one (no hidden
  retries, 30 s timeout).

### Storage

`storage/db.py`: one connection pool per database URL, checked before use so it survives a database restart (RL-7);
all connections run in UTC. An unreachable database surfaces as `temporarily_unavailable`, never as a driver error.

### Transports

- `transport/mcp_server.py` registers the three tools on an `MCPServer` subclass that turns every failure, including
  argument validation, into a JSON `{error, message}` body. It checks the client key (from the environment) on every
  call, starts the Pool and the status reporter in its lifespan, and waits at most 5 s for the startup check.
- `transport/http_app.py` is the FastAPI app: bearer-key dependencies per route, error handlers that map every failure
  to the same body, and a request log line with method, path, status and client id (never the body).
- `transport/metrics_sampler.py` and `transport/report_cli.py` feed `make sample-metrics` and `make report`.
