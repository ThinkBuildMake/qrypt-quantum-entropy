# Data flows

## Data flows

Every operation, the modules it passes through, and whether it is committed before the caller sees a result. The ids
follow `ARCHITECTURE.md`. "Where" names the code.

| # | Operation | Path | Committed before the response? | Where |
|---|---|---|---|---|
| D-1 | Connect with a client key | the launcher puts `MCP_CLIENT_KEY` in the server's environment; `main()` authenticates it as `client`, refuses to start on failure; every call authenticates again | n/a | `mcp_server.main`, `Access.authenticate` |
| D-2 | `get_random_bytes` | tool, authenticate, validate, `Pool.take`, one audit transaction, value and metadata | yes | `Issuance.get_random_bytes` |
| D-3 | `random_int` | as D-2 with one or more takes (rejection sampling) | yes | `Issuance.random_int` |
| D-4 | `generate_token` | as D-2, plus the token hash in the same transaction when an owner is given | yes | `Issuance.generate_token`, `Tokens.store_token` |
| D-5 | Chat turn | user, agent, OpenRouter, agent, MCP tool (D-2 to D-4), agent, OpenRouter, reply check, user | yes (the tool's writes) | `agent/chat.py` |
| D-6 | `POST /verify` | authenticate (client or admin), `Tokens.redeem`, one transaction: lock the owner, count failures, flip `used_at`, record the outcome | yes | `http_app.verify`, `Tokens.redeem` |
| D-7 | `GET /admin/issuances/{reference}` | authenticate (admin), read `issuances`, `issuance_batches`, `batches`, `credentials`, `tokens` | n/a | `Issuance.get_issuance` |
| D-8 | `GET /admin/usage` | authenticate (admin), read `budget`, `issuances`, `credentials` | n/a | `Meter.get_usage` |
| D-9 | `POST /admin/clients`, `POST /admin/clients/{id}/revoke` | authenticate (admin), write `credentials` | yes | `Access` |
| D-10 | `PUT /admin/budget` | authenticate (admin), update `budget.cap_kib` | yes | `Meter.set_budget` |
| D-11 | `GET /health` | no key, read newest `server_status` | n/a | `Status.get_health` |
| D-12 | `GET /metrics` | authenticate (admin), read `server_status` and `budget` | n/a | `Status.get_metrics` |
| D-13 | Pool refill (system) | `Meter.reserve`, `source.fetch`, `Meter.mark_batch(received)`, `Pool.add` | yes (the reservation) | `Pool._refill_loop` |
| D-14 | Startup check (system) | as D-13 for 1 KiB at server start | yes | `Pool.start` |
| D-15 | Status report (system) | every 5 s: read Pool, Issuance and Entropy source counters, upsert `server_status` | yes | `StatusReporter` |
| D-16 | Budget alert (system) | `Meter.reserve` sees a threshold crossed, logs `budget_alert` | n/a | `Meter.reserve` |
| D-17 | Reply check (system) | the agent compares the reply with what the tools returned, logs `reply_check` | n/a | `agent/reply_check.py` |
| D-18 | Backup (system) | scheduled CI job: `pg_dump` as the `backup` role, `age` encryption in the same pipe, upload as an artifact `[unverified]` (never run) | n/a | `.github/workflows/backup.yml`, `scripts/backup.py` |

### A tool call (D-2 to D-4)

```mermaid
sequenceDiagram
  participant C as MCP client
  participant T as mcp_server
  participant I as Issuance
  participant A as Access
  participant P as Pool
  participant M as Meter
  participant S as Entropy source
  participant Q as Qrypt
  participant DB as Postgres
  C->>T: tool call
  T->>A: authenticate with the launch key, client role
  A->>DB: read credentials by key hash
  T->>I: run the tool
  I->>P: take n bytes
  alt the pool covers the call
    P-->>I: bytes and batch slices
  else the pool cannot cover it
    P->>M: reserve KiB
    M->>DB: add to used_kib and insert the batch if within the cap
    P->>S: fetch KiB
    S->>Q: GET quantum-entropy
    Q-->>S: base64 blocks
    S-->>P: bytes
    P-->>I: bytes and slices, within 1.8 s or an error
  end
  I->>DB: one transaction: issuances, issuance_batches and the token hash
  I-->>T: value and metadata
  T-->>C: value and metadata
```

Points that matter:

- The audit transaction commits before the value leaves the process. If the database cannot be written, the caller gets
  `temporarily_unavailable` and no value (RL-6). Bytes already taken from the pool are not put back and are never given
  to anyone else.
- A call that the pool cannot cover removes no bytes (VR-8); a multi-draw call that fails on a later draw discards what
  it drew.
- The audit row names the batch and byte range of every byte, including bytes rejected by sampling.
- The response carries `metadata`: `source`, `batch_id` (first batch), `batch_ids` (all), `bytes_consumed`, `reference`.

### A chat turn (D-5)

```mermaid
sequenceDiagram
  participant U as User
  participant G as Agent
  participant O as OpenRouter
  participant M as MCP server
  U->>G: issue a one-time code for user-42, valid 10 minutes
  G->>O: conversation and tool list
  O-->>G: call generate_token with owner and expiry
  G->>M: generate_token
  M-->>G: value and metadata
  G->>G: keep the value in memory, build the placeholder
  G->>O: tool result: placeholder and metadata only
  O-->>G: reply text containing the placeholder
  G->>G: swap the placeholder for the value, run the reply check
  G-->>U: reply with the real code
```

The provider receives the placeholder and the metadata (source, batch ids, byte count, reference), never the value
(CP-5). It does receive the user's text and the owner names in it; see 06.

### Redemption (D-6)

```mermaid
sequenceDiagram
  participant R as Redeemer
  participant H as http_app
  participant A as Access
  participant T as Tokens
  participant DB as Postgres
  R->>H: POST verify with owner and value
  H->>A: authenticate, client or admin
  H->>T: redeem owner and value
  T->>DB: lock the owner, count recent failures
  alt at the failure limit
    T->>DB: record rate_limited
  else under the limit
    T->>DB: conditional update of used_at, record the outcome
  end
  T-->>H: redeemed, already_used, expired, unknown or rate_limited
  H-->>R: 200, 409, 410, 404 or 429 with the outcome in the body
```

### The status report (D-15)

```mermaid
sequenceDiagram
  participant R as StatusReporter in the MCP server
  participant P as Pool
  participant I as Issuance
  participant S as Entropy source
  participant DB as Postgres
  participant H as HTTP service
  loop every 5 seconds
    R->>P: status
    R->>I: call_stats
    R->>S: request_stats
    R->>DB: upsert one server_status row
  end
  H->>DB: read the newest row for health and metrics
```

The HTTP service cannot read the MCP server's memory, so health and metrics are only as fresh as the last report; a
report older than 15 s reads as "not reporting".
