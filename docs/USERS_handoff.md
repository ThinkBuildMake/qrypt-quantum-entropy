# Handoff: design and requirement content removed from USERS.md

These items came up while defining the user but describe how the product is built, not who it serves. Carry them into requirements, metrics, stack and architecture. Each maps to a use case ID.

## Access and trust
- Two roles enforced server-side: admin (clients, budget, audit) and client (call tools). (UC-2, UC-4)
- The agent's database role can insert token hashes and audit rows but cannot read the token table, so a prompt cannot exfiltrate tokens. The agent has no tool that returns stored tokens. (UC-1)
- stdio transport takes credentials from the environment, not an OAuth flow (MCP authorization spec: stdio "SHOULD NOT" follow it). Per-client keys are therefore an application-level check. (UC-2)
- Secrets (Qrypt key, client keys, DB credentials, pepper) come from environment configuration only; ship `.env.example`; never commit the Qrypt key. (all)

## Verification and honesty
- Rejection sampling or equivalent for integers and 6-digit codes; statistical test over at least 1,000,000 draws from a mocked uniform source. (UC-1)
- A test proves no byte offset in any batch is served twice, including under concurrency. (UC-1)
- Constant-time comparison on token verification; HMAC-SHA-256 with a server-side pepper. (UC-1)
- A test greps captured logs for an issued value; random values and tokens never appear in logs, traces or errors. (UC-1, UC-3)
- Agent evaluation: golden set of 15 prompts at 90% or better tool selection; 0% fabrication measured by scanning replies against the audit log; 5 adversarial prompts all blocked or refused. Unit tests use a mocked language model. (UC-1)
- Test suite runs against a mock upstream, never live Qrypt. (all)

## Capabilities traced to use cases
| Capability | Required by |
|---|---|
| Tools: get_random_bytes, random_int, generate_token (api_key, otp_6, uuid_v4) with metadata {source, batch_id, bytes_consumed} | UC-1, UC-2 |
| Prefetching pool with low/high-water marks, single-use bytes, in-memory only, discarded on restart | UC-1 |
| Backoff with jitter on upstream 429; fail closed on 403, 500, 503, timeout, DNS | UC-1, UC-4 |
| Startup health check surfaces an invalid/expired upstream token as an operator error, not a generic 500 | UC-4 |
| Per-call maximum length; reject length <= 0, min > max, unknown kind | UC-2 |
| Global KiB meter (default 10,240), per-client usage, admin API with empty states | UC-4 |
| Append-only audit: client, tool, bytes, batch id, timestamp; never the value | UC-3 |
| `GET /health` with pool level and upstream status; structured logs; migrations; env-based config | UC-4 |
| `POST /verify` single-use redemption with expiry | UC-1 |
| Seed script: one admin, two clients, sample tokens; MCP client config snippet | UC-2 |

## Time budget
- Latency under 50 ms p95 (warm pool, 1,000 calls, mocked upstream); 100 concurrent calls with 0 errors and at most 30 upstream requests per 10 s; at least 64 KiB served per upstream request. Set targets in requirements and metrics. (UC-1)
- Core test coverage at least 80% on pool accounting, sampling, budget, token issue/verify, failure handling. (all)

## Scope boundaries with their design reasons
- Fallback to OS CSPRNG is off by default; if enabled it must label source "os_csprng" in metadata and audit and raise an alert. (UC-1)
- No stretch goals now; keep the upstream client swappable so a later second source or HTTP transport does not force a rewrite. (UC-1)

## Open questions for the audit / research step
- **Upstream rate ceiling conflict.** Qrypt docs say 30 requests per 10 s per token; the Qrypt SLA says requests above 20 per second are not valid. Work to the stricter reading and confirm with Qrypt.
- **The language model sees the token.** For the agent to show Jonas a code, the code passes through the model provider. OpenRouter logs metadata by default and prompts only if opted in, retention follows each provider, and free-model routing may reach providers that train on data. Decide how the agent returns a value without a third party retaining it (account training opt-out, provider data-policy filter, or the value never entering the model's context). Check the data policy of the specific model chosen. (UC-1)
- **Hosting vs Qrypt Terms 1.8(a).** The Terms bar making the Software available as an internet/cloud service. The required deployed endpoints (verify, admin, health) expose no entropy, but Streamable HTTP (a stretch item) would. Confirm with Qrypt before any hosted MCP endpoint.
- **Are the random bytes "Qrypt Properties"?** Terms 1.8(c) bars making them available; Terms and docs are silent on caching and in-memory pooling. Confirm with Qrypt that serving pooled bytes to internal clients is allowed.
- **Internal clients vs "End Users".** Terms 1.4 defines End Users as employees and authorised contractors. The two seeded clients are test fixtures; confirm no third-party clients draw on the owner's token.
- **Qrypt allotment visibility.** Check whether Qrypt's portal shows usage per token, which weakens or supports UC-4's case for an own meter.
- **Is Qrypt validated** under SP 800-90B? Not checked.
- **Schedule risk.** Deadline is Tue 2026-10-20 11:59 PM CST with nothing to be cut and one builder. Requirements and stories should size against this; if the estimate does not fit, flag the tradeoff to the owner rather than silently dropping a core item.
- **Qrypt trial terms** may be withdrawn at any time (Terms 3.3); the Terms may change at any time (10.4).

## Existing products in the same space
- A "secret generator" MCP server exists in public listings (mcpmarket.com) and an audited skill for remote seed generation (clawhub.ai). Neither was reviewed for pricing or limits; check before claiming a gap.
