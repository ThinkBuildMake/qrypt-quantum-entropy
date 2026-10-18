# Security and data handling

## Security and data handling

### What is protected, and how

| Asset | Protection | Where | Checked by |
|---|---|---|---|
| Qrypt token (`QRYPT_API_KEY`) | environment only; held by the MCP server; a secret `SecretStr`, registered with the log filter; never committed | `config.py`, `upstream/source.py`, `logs.py` | `make secret-scan` over tree and full history; `make leak-scan` |
| Client and admin keys | 32 random bytes from the operating system; stored only as HMAC-SHA-256 under `KEY_HASH_SECRET`; shown once | `service/access.py` | `tests/test_access.py` |
| Token values | stored only as HMAC-SHA-256 under `PEPPER` of owner and value; compared in constant time; never logged | `service/tokens.py` | `tests/test_tokens_store.py`, gate tests |
| Pooled entropy | memory only, never a file, log line, error message or table; discarded at stop | `service/pool.py` | `make leak-scan` searches files, logs, responses, tables, a dump, the tree and git history for known markers (CP-2) |
| Audit record | append-only for every application role; holds client, tool, byte count, batch and byte ranges, time; never a value | schema and grants (04) | `tests/test_dbpolicy.py` |
| Token table | the issuing role can insert but not read it; `used_at` can be set once and nothing else changes | grants and the `tokens_used_flag_once` trigger | `tests/test_dbpolicy.py` |
| Budget | one database row; only rises through the application role; the cap is writable only by `http_service` | grants, `budget_meter_only_rises` | `tests/test_meter.py`, two-process gate |
| Issued values on the way to a user | the agent keeps them out of the model's context | `agent/chat.py` | `make agent-capture-check`, `tests/test_agent.py` |

### Authentication and roles

Two roles enforced server-side (AZ-1). `client` may call the tools and `POST /verify`; `admin` may call `/verify` and
every `/admin/*` route and `/metrics`. `/health` is open. A revoked key is refused on its next call. Role checks are
application code; the database roles in 04 are a second, independent limit on what each process can do.

### Logging

One JSON object per line with timestamp, level, event and client id when known. The MCP server logs to its error stream
(standard output carries the protocol). `logs.py` redacts by field name (`value`, `key`, `token`, `bytes`, `secret`,
`password`, `authorization`, `random`, ...) and, for every secret and issued value registered with `register_secret`,
replaces the raw, hex, base64 and JSON-escaped forms wherever they appear in a line. Request lines carry method, path,
status and client id, never a body. Counters and durations are logged; values are not.

### Transport security

TLS for the hosted service is terminated by Render, which redirects plain HTTP to HTTPS `[unverified]` (never observed:
no Render account). The MCP server is a local stdio process and has no network listener. The mock and the local runs use
plain HTTP on localhost.

### Backup

A daily GitHub Actions job dumps the database as the read-only `backup` role and encrypts it with `age` in the same
pipe; no plain dump touches a disk. The private key stays offline. The dump holds client-key HMACs, token HMACs and
audit rows, never the pepper, a key or a value. `[unverified]`: the workflow and a real key pair have never run; the local
`make restore-drill` ran against Postgres 16 (`docs/BACKUP.md`, `docs/DEVIATIONS.md` 14).

### Data handling summary

| Data | Where it lives | How long |
|---|---|---|
| Random bytes | MCP server memory | until served or the process stops |
| Issued values | returned to the caller; the agent holds them in memory for one conversation | not stored by the service |
| Token hashes, key hashes, audit rows, outcomes | Postgres | the database's life (30 days on the free tier, 08) |
| Logs | stdout or `logs/*.log` locally | not managed here |
| Personal data | none: all names are synthetic (GC-8), no real customer data is used or stored | |

## Model provider data policy

**What the agent sends to the model provider** (CP-5). Through OpenRouter, in each chat request: the system prompt, the
user's text, the tool list, earlier model messages, and tool results made of a placeholder (such as `{{v1}}`) plus the
metadata (source, batch ids, byte count, reference). It never sends an issued value: the agent swaps the placeholder
for the value after the model has answered, and swaps held values back to placeholders in anything the user types
(values shorter than six characters excepted). It does send what the user types, including owner names such as
`user-42`. `make agent-capture-check` verifies a saved scripted capture holds placeholders only; the test is
against a scripted model, so it shows the agent's behaviour, not any real provider's.

**Model.** Default `google/gemma-4-31b-it:free` (`AGENT_MODEL`, `agent/settings.py`), reached through OpenRouter's
OpenAI-compatible API. Alternatives named in `STACK.md` are `poolside/laguna-s-2.1` and
`nvidia/nemotron-3-super-120b-a12b`. The choice was to be confirmed by a trial against the golden set; no trial against
the real model has been run on this build `[unverified]`. The serving provider behind a `:free` route is chosen by
OpenRouter and is not recorded in this repository.

**Retention.** Not established for this model `[unverified]`. What was read: OpenRouter's privacy documentation
(fetched 2026-10-18) says each provider has its own data-handling policy, that OpenRouter does not route by retention
policy, and that per-provider retention appears in a table the documentation loads dynamically (not captured). The
project's own notes say OpenRouter logs metadata by default and prompts only if opted in (`USERS_handoff.md`,
`[unverified]`). Before real use, read the serving provider's retention figure from the model's endpoint page and write
it here.

**Training.** Not established for this model `[unverified]`. `STACK.md` records that some free providers may use
prompts for training. OpenRouter's documentation says the account has separate training settings for paid and free
models, and that the setting does not govern OpenRouter's own handling of prompts.

**Opt-out.** The training opt-out is an account-level OpenRouter setting (privacy settings, the free-model switch),
listed as a go-live prerequisite in `STACK.md` 19. It has not been confirmed as set `[unverified]`; the owner does this
in the OpenRouter account. A per-request provider data-policy filter is also available and is not used by the agent.

**What this means.** Even in the worst case (a provider retains and trains on everything it receives), no issued value,
key, token or pooled byte is exposed through the model, because none is sent. User prompts and owner names would be.
Values used in demos are fictional and short-lived (CP-5: 10 minutes or less).

## Open questions for Qrypt

These are unanswered by Qrypt and affect what is allowed. None has been put to Qrypt in this build. Trial terms may be
withdrawn (Terms 3.3) or changed (10.4).

| Question | Why it matters | What the build does meanwhile |
|---|---|---|
| Are the random bytes "Qrypt Properties"? (Terms 1.8(c) bars making Qrypt Properties available) | If they are, serving them to clients may need permission | Treated as an open risk; only internal test clients are seeded; GC-8 limits use to assessment |
| Is pooling allowed? The Terms and docs are silent on caching bytes in memory and serving them later | The design depends on pooling to serve 64 KiB or more per upstream request and to stay inside the request limit | Pooling is built and used; bytes stay in memory only and are served once |
| What is the true rate ceiling? Qrypt documents 30 requests per 10 s per token; its SLA says requests above 20 per second are not valid | The two readings differ | The stricter documented reading is enforced: at most 30 requests in any 10 s (DE-1). The mock enforces the same; live behaviour `[unverified]` |
| Does hosting break Terms 1.8(a) (no offering the software as an internet or cloud service)? | A hosted entropy endpoint would | Only endpoints that return no entropy-derived value are hosted (GC-7); `make check-routes` calls each hosted route and confirms none returns one |
| Are the two seeded clients "End Users" under Terms 1.4? | Third-party clients would not be | They are test fixtures (`billing-agent`, `support-agent`) |
| Does Qrypt's portal show usage per token, and is the service validated under SP 800-90B? | The own meter is justified only if the portal does not show it; the validation status is a quality claim | Not checked; the own meter is kept |

Hosted-route and live-use constraints that follow: live Qrypt use across the whole assessment is capped at 10,240 KiB
(GC-4), enforced by the meter, and each database has its own meter, so KiB spent against an earlier database still count
against the cap by hand (`docs/DEPLOYMENT_GUIDE.md`).

### Where the least-privilege story is weaker than it reads

- The MCP server's database role runs wherever the caller runs the server. Whoever controls that process can insert
  audit rows for any client, raise `used_kib`, and write token hashes. The database stops it reading or changing tokens
  and audit rows already written. This is the trust boundary of the issuing role (08).
- The hosted service and the MCP server connect as their own roles (`HTTP_SERVICE_URL`, `MCP_SERVER_URL`); a `prod`
  process refuses to start without its string. The owner's string (`MIGRATOR_URL`) is still in the Render service's
  environment because the free plan migrates at start, but the service process does not read it `[unverified on Render]`.
- Failed-redemption limits are per owner, not per caller (08).
