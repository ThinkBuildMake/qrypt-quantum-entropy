# API reference

## API reference

Two surfaces: the MCP tools (stdio, the entropy-bearing surface, never hosted) and the HTTP routes (hosted, none of
which returns an entropy-derived value, GC-7). `make docs-check` compares the `### Route:` and `### Tool:` headings below
with the routes the FastAPI app and the tools the MCP server actually register, in both directions.

### Error format

HTTP errors and tool errors carry the same two fields: `{"error": "<code>", "message": "<text>"}`. A message never
contains a value, a key or a hash. A tool error is delivered as an MCP tool error whose text is that JSON.

| Code | HTTP | Meaning |
|---|---|---|
| `invalid_argument` | 400 | Bad input. Names the fields, never the values. Also used for 405 (wrong method) |
| `unauthorized` | 401 | Missing, unknown or revoked key (one message for all three) |
| `forbidden` | 403 | Valid key, role not allowed here |
| `not_found` | 404 | No such client, issuance or route |
| `budget_exhausted` | 503 | The entropy budget is spent, or Qrypt's allotment is used |
| `temporarily_unavailable` | 503 | Qrypt, the database or the pool could not serve the call in time; also any unexpected failure |
| `upstream_misconfigured` | 503 | The Qrypt token is invalid or expired (operator error) |
| `database_unavailable` | 503 | `GET /health` only: the database cannot be reached |
| `already_used`, `expired`, `unknown`, `rate_limited` | 409, 410, 404, 429 | `/verify` refusals; the body also has `"outcome"` |

### Authentication

HTTP: `Authorization: Bearer <key>`. The roles are `admin` and `client`. `/health` takes no key; the schema and interactive documentation routes (`/openapi.json`, `/docs`, `/redoc`) are switched off.
`/verify` takes a client or admin key; every other route takes an admin key. MCP: the client key is in the server's
environment (`MCP_CLIENT_KEY`) and is checked at start and on every call; only the `client` role may call the tools, so an
admin key is `forbidden` there.

## MCP tools

Every tool returns an `IssuedValue`: `{"value": <string or integer>, "metadata": {...}}` with

| Metadata field | Meaning |
|---|---|
| `source` | the entropy source label of the first batch used (`qrypt`) |
| `batch_id` | the first batch the bytes came from |
| `batch_ids` | every batch the bytes came from, in order |
| `bytes_consumed` | bytes drawn for this call, rejected draws included |
| `reference` | the issuance id; the key to `GET /admin/issuances/{reference}` and `make trace REF=` |

### Tool: get_random_bytes

| Argument | Type | Rule |
|---|---|---|
| `length` | integer | 1 to `PER_CALL_MAX_BYTES` (default 1024) |
| `encoding` | `"hex"` or `"base64"` | required |

Returns the bytes as a string in that encoding. Errors: `invalid_argument`, `unauthorized`, `forbidden`,
`budget_exhausted`, `temporarily_unavailable`, `upstream_misconfigured`.

### Tool: random_int

| Argument | Type | Rule |
|---|---|---|
| `min` | integer | within +/- (2^53 - 1) |
| `max` | integer | within +/- (2^53 - 1), at least `min` |

Returns an integer in the inclusive range, by rejection sampling. Same errors as above.

### Tool: generate_token

| Argument | Type | Rule |
|---|---|---|
| `kind` | `"api_key"`, `"otp_6"` or `"uuid_v4"` | required |
| `owner` | string or null | at most 256 characters, no NUL; when given the token is stored as a hash and can be redeemed |
| `expiry_seconds` | integer or null | 1 to `TOKEN_EXPIRY_MAX_SECONDS` (86,400); needs `owner`; default 600 when `owner` is given |

Returns a string: 64 hex characters (`api_key`), six digits (`otp_6`) or a version 4 UUID. Same errors as above. A token
issued without an owner is returned once and nothing about its value is stored.

## HTTP routes

All bodies are JSON unless stated. A request that fails validation gets 400 `invalid_argument` naming the fields.

### Route: GET /health

No key. Returns the newest status report from the MCP server.

```json
{"reporting": true, "reportAgeSeconds": 2.1,
 "pool": {"levelBytes": 98304, "lowMarkBytes": 32768, "highMarkBytes": 131072},
 "upstream": {"status": "ok", "lastSuccessAt": "2026-10-18T12:00:00+00:00"}}
```

`upstream.status` is `ok`, `degraded`, `failing` or `misconfigured`. When no report is newer than 15 s the answer is still
200 with `{"reporting": false, "reportAgeSeconds": null, "pool": null, "upstream": null}`. 503 `database_unavailable`
when the database cannot be reached.

### Route: GET /metrics

Admin key. Prometheus text (`text/plain`). Always present: `qrypt_report_age_seconds`, `qrypt_budget_used_kib`. Present
only while the MCP server is reporting: `qrypt_pool_level_bytes`, `qrypt_upstream_requests_last_10s`,
`qrypt_process_start_time_seconds`, `qrypt_calls_total{tool}`, `qrypt_errors_total{code}`,
`qrypt_tool_call_duration_ms{quantile}` (0.5, 0.95, 0.99).

### Route: POST /verify

Client or admin key. Redeems a token once.

Request: `{"owner": "<1 to 256 characters, no NUL>", "value": "<1 to 4096 characters>"}`.

| Result | HTTP | Body |
|---|---|---|
| redeemed | 200 | `{"outcome": "redeemed"}` |
| no such token for that owner | 404 | `{"error": "unknown", "message": "the code was not redeemed", "outcome": "unknown"}` |
| already used | 409 | same shape, `already_used` |
| expired | 410 | same shape, `expired` |
| five failed attempts for this owner in 10 minutes | 429 | same shape, `rate_limited` |

Every well-formed authenticated attempt records one `redemption_outcomes` row. Other errors: 400, 401, 403, 503
(`temporarily_unavailable`, including when too many attempts for one owner queue for more than 2 s).

### Route: GET /admin/usage

Admin key. The budget and the bytes consumed per client.

```json
{"budget": {"capKib": 10240, "usedKib": 64},
 "perClient": [{"clientId": "<uuid>", "name": "billing-agent", "bytesConsumed": 128}]}
```

`perClient` is empty before any issuance.

### Route: PUT /admin/budget

Admin key. Request `{"capKib": <integer, 0 to 2^63 - 1>}` (a strict integer, not a string or float). Returns
`{"budget": {"capKib": ..., "usedKib": ...}}`. Lowering the cap below use is accepted and leaves the budget exhausted.

### Route: GET /admin/issuances/{reference}

Admin key. `reference` must be a UUID (400 otherwise); 404 `not_found` when no such issuance. Returns the provenance:

```json
{"reference": "<uuid>", "client": {"id": "<uuid>", "name": "billing-agent"}, "tool": "generate_token",
 "bytesConsumed": 3, "createdAt": "2026-10-18T12:00:00+00:00",
 "batches": [{"batchId": "<uuid>", "source": "qrypt", "byteOffset": 0, "byteCount": 3, "requestedAt": "..."}],
 "token": {"owner": "user-42", "kind": "otp_6", "expiresAt": "...", "usedAt": null, "isSeed": false}}
```

`token` is null when no token was stored for the issuance. No value and no hash is ever returned.

### Route: POST /admin/trace-token

Admin key. Request `{"owner": "<1 to 256 characters>", "value": "<the token>"}`, the same shape as `POST /verify`. The
value is hashed with the token pepper (the way redemption does), compared with the owner's stored hashes, and never
logged or returned. 200 with the same body as `GET /admin/issuances/{reference}` for the issuance that produced the
token; this is how an admin holding only an owner and a code finds the reference. 404 `not_found` ("no such token") for
an unknown token and for a seed token, which has no issuance, so the answer says nothing more. It is not a
redemption (the token is not marked used). A failed lookup counts toward a per-owner limit with the same size and window
as `POST /verify` (AZ-6) but on its own in-memory count, not in `redemption_outcomes`; at the limit it answers 503
`temporarily_unavailable`. A seed token traced with the right value is not counted as a failure. The request
shape is checked before the key, as on every route here (a malformed body from an unauthenticated caller gets 400, never
any token information). `TRACE_VALUE=<code> make trace OWNER=<owner>` does the same lookup from the command line (needs
`DATABASE_URL` and `PEPPER`; the value comes from the environment so it stays out of the process list).

### Route: POST /admin/clients

Admin key. Request `{"name": "<1 to 256 characters>"}`. 201 with
`{"client": {"id": "<uuid>", "name": "...", "role": "client", "status": "active"}, "key": "<shown once>"}`. 400 when the
name is taken. The key is not recoverable afterwards.

### Route: POST /admin/clients/{client_id}/revoke

Admin key. Returns `{"client": {"id": "...", "name": "...", "role": "client", "status": "revoked"}}`. Revoking again keeps
the first revocation time. 404 `not_found` for a malformed id, an unknown id or an admin credential. A revoked key is
refused on its next call (MCP server and HTTP).
