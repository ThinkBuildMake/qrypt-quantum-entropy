# REQUIREMENTS.md — Quantum Entropy MCP Server & Secure Token Agent

**Status:** draft (becomes `v1.0 — handed to architecture <date>` at handoff; row changes after that bump the version)

**Purpose.** Turn the users doc (`USERS.md`, who and how) and its handoff (`USERS_handoff.md`) into testable requirements and constraints with numeric thresholds, so the key-metrics doc can measure against these numbers, the stack doc can choose technology against them and the architecture doc can design to them. Every requirement traces to a use case, a constraint, or a hard gate from the assessment brief or PRD.

**Scope.**

*This document decides:* what the system must do per use case in the minimum product (all five core capabilities; nothing is cut), the "must never" rules, numeric thresholds for quality attributes, how each is verified, and what is out of scope by design.

*This document defers:*
- Technology choices and tradeoffs → the stack document.
- Components, capacity, modules, schemas, contracts, data flow, and the techniques used to meet a budget → the high-level design and architecture documents.
- Why the problem exists and where the cut line is → `USERS.md` (it stands in for a problem statement; see Assumption 1).
- Flows, personas and study figures → `USERS.md` (cited here, not restated).
- Which requirements become tracked metrics and dashboards → the key-metrics document.
- Later iterations (per-client quotas, HTTP transport, dashboard, second-language client, OS-generator fallback) get no functional rows; the owner wants none now. Section 3.12 holds the tests the current build must pass so they stay cheap to add.

*Not turned into rows:* the brief's illustrative "8 hours to 1 hour per review, $3,780 a year" saving. It is an assumption in the brief, not something the system can be tested against.

If a row below names a library, a component, or a way of meeting a threshold, it is in the wrong document.

**How to read a requirement.**

| Field | Meaning |
|---|---|
| **Requirement** | *Functional:* from the user's side; no number. *Non-functional and given:* from the system's side; the requirement is the number itself, tagged. If the number isn't met, the requirement fails. |
| **Verified by** | Non-functional and given rows only. Functional rows are verified by a user seeing or using the feature. |
| **Trace** | `U§n` = `USERS.md` section n (UC-n for use cases). `B-…` = the assessment brief: `B-C1`…`B-C5` core requirements 1–5, `B-EC` edge cases, `B-PB` performance benchmarks, `B-AI` AI metrics, `B-SEC` security, `B-CQ` code quality, `B-SUB` submission, `B-QR` Qrypt quick reference. `P` = the PRD. |
| **Priority** | **Gate** = must hold before real data. **Must** = required for the product to be done. **Should** = required unless a documented tradeoff says otherwise. This document does not cut scope for a demo; if a Gate or Must is deferred to fit the timeline, the owner decides when stories are written. |

**Where the numbers come from.** **[src]** = from a cited source, or computed from one with the arithmetic shown, or stated by the owner. **[prop]** = proposed engineering target, the owner's to change with a stated reason. **[tbd]** = cannot be set until something exists or is decided; the row says what will set it. Section 5 lists every [prop] and [tbd].

**Assumptions.**
1. No separate problem statement exists. `USERS.md` §2 (problem), §3 (why these moments) and §7 (out of scope) stand in for it.
2. The brief names an `encoding` parameter but not its values. This document assumes `hex` and `base64`.
3. A redemption request carries the owner and the value presented. The brief says only that `POST /verify` redeems once.
4. The admin API and `POST /verify` are one HTTP service (the brief asks for one deployed URL for "admin + verify").
5. "Mock" means a committed stand-in for the Qrypt service that reproduces its response shape, errors and rate limit, so the whole suite runs offline.
6. The owner wants no stretch items now (owner, 2026-10-17).

**Definitions.**

| Term | Meaning |
|---|---|
| **Value** | What a tool returns to a caller: bytes, an integer, or a token string. Never stored and never logged. |
| **Token** | A credential the Secure Token Agent issues to an end user, stored only as a hash with owner, expiry and a single-use flag. A token is one kind of value. |
| **Issuance** | One successful tool call that returns a value. It produces exactly one audit record. |
| **Batch** | One response from the upstream service: a run of 1–512 KiB blocks, identified by a batch id. |
| **KiB** | 1,024 bytes. The brief's "10 MB" equals 10,240 KiB. |
| **Pool** | The in-memory store of fetched, not-yet-served entropy. **Pool level** = bytes held. |
| **Warm pool** | A pool whose level is at or above the low-water mark. |
| **Budget** | The cap on KiB fetched from upstream (default 10,240). It counts KiB *fetched*, not KiB served; fetched bytes discarded at restart still count. |
| **Bytes consumed** | Every byte removed from the pool for a call, including bytes thrown away by rejection sampling. |
| **Fail closed** | Return an error and no value; never substitute another source. |
| **Client** | An MCP caller with its own key (an agent or Priya's tooling). **Admin** = the operator role. |
| **Fabrication** | A value shown to a user in an agent reply that no tool returned in that run. |

---

## 0. Requirements summary (one screen)

| Area | Headline threshold |
|---|---|
| Authorization | 2 roles enforced server-side; 0 tool calls without a valid client; the agent has 0 read paths to stored tokens |
| Correctness | 0 bytes served twice (100 concurrent callers); 1,000,000-draw bias test passes and fails a modulo sampler; token redeems exactly once; budget never exceeded |
| Performance | p95 < 50 ms warm (1,000 calls); 100 concurrent callers, 0 errors; ≥ 64 KiB served per upstream request |
| Upstream | ≤ 30 requests per 10 s; 429 backoff with jitter; every upstream fault fails closed |
| Privacy | 0 values in logs, errors, audit rows, disk, or the repo; pool in memory only |
| Evaluation | golden set ≥ 14/15; fabrication 0; adversarial 5/5; ≥ 80% coverage on core logic; suite runs offline |
| Cost | $0 paid; live Qrypt use ≤ 10,240 KiB in total |
| Delivery | due Tue 2026-10-20 23:59 Central; clean-clone quick-start ≤ 10 min |

---

## 0a. Key decisions

| # | Decision | Alternative rejected | Where |
|---|---|---|---|
| 1 | The budget counts KiB *fetched* from upstream. | Counting only KiB served: it would let pool waste and restarts hide spend against the 10,240 KiB cap. | VR-8, SC-1, CO-1 |
| 2 | After the budget is spent, bytes already in the pool may still be served; a call that the pool cannot cover in full fails whole and consumes nothing. | Refusing every call the moment the meter reads zero: it throws away entropy already paid for. Needs confirmation (open item 5). | FR-5.5, VR-9 |
| 3 | Fail closed on every upstream fault; v1 has no fallback source. | Automatic OS-generator fallback: it defeats what Priya is buying (`U§7`). | RL-2, FR-6.1, CH-1 |
| 4 | The upstream ceiling is the brief's 30 requests per 10 s (3 per second). | The SLA's "20 per second", which is looser (200 per 10 s). Working to the stricter limit satisfies both readings. | DE-1 |
| 5 | The agent has no way to read stored tokens. | A "look up my tokens" tool: a prompt could abuse it (`U§7`). | AZ-3, AZ-4, FR-2.7 |
| 6 | The audit record holds no value, so fabrication is checked by comparing agent replies with the values the tools returned in the same test run. | Keeping a value or hash of every random output in the audit log. | EV-2, CP-3 |
| 7 | Whether issued values enter the model provider's context is not yet decided; interim: they do, and the model's data policy is recorded. | Deciding now. | CP-5 |
| 8 | Repeated failed redemptions for one owner are limited (an addition beyond the brief). | Unlimited attempts against a 6-digit space. The cost is that an attacker can lock an owner out for a window. | AZ-6 |

---

## 1. Givens

*Inputs the project arrived with. Read these before the requirements.*

### 1.1 Given constraints (GC-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| GC-1 | Delivered by Tuesday 2026-10-20 23:59 Central, ready for review Wednesday 2026-10-21, by one builder. All five core capabilities ship; nothing is designated to drop first [src owner, 2026-10-17]. "Central" is read as the earlier of CST and CDT, so the cut-off is 2026-10-21 04:59 UTC (CDT) [prop]. | Last submitted commit timestamp ≤ 2026-10-21 04:59 UTC; contributor list shows 1 author | U§8 | Must |
| GC-2 | $0 paid spend. No paid API key is needed to build, run, grade or test. Vendors from the owner's preferred list are optional and used only on free tiers [src owner]. | Clean-clone run with no paid keys passes; billing pages show $0 | U§8, P Off-Limits | Must |
| GC-3 | The agent's language model is called through OpenRouter [src owner], must support tool calling, and a free path works (free-tier model or local model). The golden and adversarial runs (EV-1, EV-3) fit inside that model's free-tier request limits, which are recorded in the results file. | Results file lists model name, its free-tier limit, and the request count used | U§8, B-Tech | Must |
| GC-4 | Qrypt is the only entropy source [src owner: mandatory]. Live Qrypt use across the whole assessment is ≤ 10,240 KiB, including development, demo and any grader smoke test; 0 KiB were used as of 2026-10-17 [src owner]. | Budget meter reading at submission, cross-checked against Qrypt's portal if it shows usage | U§8, B-QR, P Off-Limits | Must |
| GC-5 | The server is written in exactly one of Python, TypeScript/Node, Go, Rust or C++; data is held in a SQL database with a free tier that supports separate roles and permissions, so AZ-3 and VR-10 can be enforced by the database. SQLite is not used (owner, 2026-10-17); the PRD allows "SQLite or any SQL database with free tier" [src B-Tech]. The same engine runs locally and in CI so the suite stays offline. | Repository language statistics; database engine and free-tier plan named in the deployment guide; AZ-3 and VR-10 tests pass against that engine | B-Tech, P | Must |
| GC-6 | The server uses an official MCP SDK and runs over stdio; MCP Inspector connects to it and lists the tools [src B-Tech]. | Recorded Inspector session in the demo video | B-Tech | Must |
| GC-7 | Only endpoints that return no entropy-derived value are hosted: `POST /verify`, the admin API and `GET /health`. No hosted endpoint returns random bytes, integers or issued tokens, so the Qrypt Terms 1.8(a) bar on offering the software as a cloud service is not tripped. | Route list of the deployed service reviewed; scripted request to each hosted route confirms none returns a tool value | U§8, `USERS_handoff.md` | Gate |
| GC-8 | Use is assessment only: seeded and demo data is fictional, no real customer data, no commercial or public use (Qrypt Terms 1.7a). | Seed script and fixtures contain 0 real personal data (review) | U§8 | Gate |
| GC-9 | The hosted HTTP API runs on a free-tier host [src B-Tech]. | Hosting plan named in the deployment guide shows free tier | B-Tech, P | Must |

*(Team size is part of GC-1.)*

### 1.2 Dependencies and environment (DE-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| DE-1 | At most 30 requests to Qrypt in any 10-second window, however many callers there are [src B-QR]. The SLA's looser 20 per second is not relied on (Decision 4). | Mock upstream records request timestamps during the 100-caller load test; max count in any sliding 10 s window ≤ 30 | B-QR, B-C2 | Gate |
| DE-2 | Every upstream request asks for 1 to 512 KiB [src B-QR]; the client never sends a size outside that range, so Qrypt's 400 is unreachable in normal operation. | Unit test over boundary sizes 0, 1, 512, 513 shows nothing is sent outside 1–512 | B-QR | Must |
| DE-3 | After an upstream 429 the first retry waits ≥ 0.5 s, each further consecutive 429 at least doubles the nominal wait (1, 2, 4, 8, 16, 32 s, capped at 60 s), and each wait is randomised to between 50% and 100% of nominal [prop]. During 60 s of continuous 429 the client makes ≤ 8 upstream requests (worst case waits of 0.5 + 1 + 2 + 4 + 8 + 16 + 30 s put the 8th attempt past 60 s, so ≤ 7 occur). | Mock returns 429 for 120 s; request log counts ≤ 8 in the first 60 s; waits differ between two runs with different jitter seeds | B-EC | Must |
| DE-4 | An upstream request that has not answered within 5 s [prop] counts as a failure and is handled as a 5xx. | Mock delays a reply 6 s; the caller receives the unavailable error and the request is abandoned | B-EC | Must |
| DE-5 | At startup the server makes one check request of ≤ 1 KiB [prop]; an invalid or expired Qrypt token (401) is reported as an operator configuration error naming the cause, and `GET /health` reports upstream status "misconfigured". It is never shown to a caller as a generic 500. | Mock returns 401 at startup; log event and `/health` body name the cause; no end-user call returns 500 | B-EC, U§5 UC-4 | Must |

---

## 2. Functional requirements

*Every functional requirement is verified the same way: a user can see or use the feature at the story's demo. Functional rows carry no number; what the system has to accommodate for them is in §3, traced back here.*

### FR-1 Tools that issue values (UC-1, UC-2)

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-1.1 | The agent developer can ask for a number of random bytes in a chosen encoding (hex or base64) and gets the value back. | UC-1, B-C1 | Must |
| FR-1.2 | The agent developer can ask for a random integer between a minimum and a maximum, both inclusive, and gets one inside that range. | UC-1, B-C1 | Must |
| FR-1.3 | The agent developer can ask for a token of kind `api_key`, `otp_6` (six decimal digits, leading zeros kept) or `uuid_v4` and gets one in that format. | UC-1, B-C1 | Must |
| FR-1.4 | Every returned value comes with its source, its batch id and the bytes consumed. | UC-1, UC-2, B-C1 | Must |
| FR-1.5 | A call with bad input (length of zero or less, length above the per-call maximum, minimum above maximum, unknown token kind, expiry of zero or less) is refused with a plain message saying what was wrong. | UC-2, B-EC | Must |
| FR-1.6 | When the entropy source is temporarily unavailable and the pool cannot cover the call, the caller gets a "temporarily unavailable" message and no value. | UC-1, B-EC | Must |

### FR-2 The Secure Token Agent (UC-1)

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-2.1 | An end user types a plain-language request into the agent's command-line interface such as "issue a one-time reset code for user 42 that expires in 10 minutes" and the agent issues a token for that owner with that expiry and shows it to the user. If no expiry is given the agent applies a default (10 minutes [prop]). | UC-1, B-C3 | Must |
| FR-2.2 | The agent also answers requests for random bytes, a random integer, or an API key by calling the matching tool (for example "give me 32 random bytes as hex", "new API key for the billing service"). | UC-1, B-AI | Must |
| FR-2.3 | When asked to make up a code, the agent declines and offers to issue a real one. | UC-1, B-AI | Must |
| FR-2.4 | An issued token can be redeemed once through `POST /verify`. | UC-1, B-C3 | Must |
| FR-2.5 | A second redemption of the same token is refused. | UC-1, B-C3 | Must |
| FR-2.6 | A redemption after the token's expiry is refused. | UC-1, B-C3 | Must |
| FR-2.7 | When asked to show other users' tokens, or every token issued today, the agent refuses and shows none. | UC-1, B-EC | Gate |
| FR-2.8 | When no value can be issued (entropy unavailable or budget spent) the agent tells the user so and does not produce a value itself. | UC-1, U§4 9:55 PM | Gate |

### FR-3 Connect and inspect (UC-2)

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-3.1 | With her client key, the agent developer opens a stdio MCP connection and sees all tools with their expected inputs in MCP Inspector. | UC-2, B-C1 | Must |
| FR-3.2 | A connection with a missing, unknown or revoked client key is refused with a clear message and no tool runs. | UC-2, B-C4 | Gate |
| FR-3.3 | The agent developer can copy a ready-made JSON connection snippet from the README into an MCP-capable client and connect. | UC-2, B-SUB | Must |

### FR-4 Trace an issued value (UC-3)

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-4.1 | An admin can look up an issuance by reference and see which client asked, which tool, when, how many bytes, and which batch. | UC-3, B-C5 | Must |
| FR-4.2 | For any issued token, an admin can follow it to its audit record and from there to the upstream batch. The reference used is never the value. | UC-3, B-C5 | Must |
| FR-4.3 | No one, admin included, can edit or delete an audit record through the product. | UC-3, B-C5 | Must |

### FR-5 See and cap entropy use (UC-4)

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-5.1 | An admin sees total KiB used against the budget and bytes consumed per client. | UC-4, B-C4 | Must |
| FR-5.2 | An admin can create a client and receives that client's key. | UC-2, UC-4, B-SEC | Must |
| FR-5.3 | An admin can revoke a client; its next call is refused. | UC-4 | Must |
| FR-5.4 | An admin can set the budget; the default is 10,240 KiB. | UC-4, B-C4 | Must |
| FR-5.5 | Once the budget is spent and the pool cannot cover a call, the call returns a clear budget-exhausted error and no value. | UC-1, UC-4, B-C4 | Gate |
| FR-5.6 | With no clients, tokens or audit rows, every admin view and `GET /health` shows an empty state, not an error. | UC-4, B-EC | Must |
| FR-5.7 | An operator can call `GET /health` and see the pool level and the upstream status. | UC-4, B-CQ | Must |
| FR-5.8 | An operator whose Qrypt token is invalid or expired sees that named as a configuration problem at startup. | UC-4, B-EC | Must |

### FR-6 Not in v1

| ID | Requirement | Trace | Priority |
|---|---|---|---|
| FR-6.1 | There is no setting that makes the server use another random source. Entropy that cannot come from Qrypt yields an error (later-iteration #5). | U§6, U§7 | Must |
| FR-6.2 | There is no per-client daily quota, remote (HTTP) MCP connection, admin dashboard, or second-language client (later iterations #1–#4). | U§6 | Must |

---

## 3. Non-functional requirements

*What the system has to accommodate. Columns: ID · Requirement · Verified by · Trace · Priority. Groups with no row are absent because nothing in the users doc needs them: there is no usability, accessibility or localisation group (the product has no end-user screens, `U§7`).*

### 3.1 Authorization (AZ-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| AZ-1 | Two roles, admin and client, are enforced on the server. Across every tool and HTTP route, with {no key, client key, admin key}, 0 calls succeed outside the role that route allows. | Committed matrix test over every route and tool × 3 credentials | B-SEC, FR-5, FR-3.2 | Gate |
| AZ-2 | 0 tool calls run with a missing, unknown or revoked client key. A rejected call draws 0 bytes and uses 0 KiB of budget. A revocation takes effect for the very next call. | Test: call with each bad credential, assert pool level and meter unchanged; revoke, then call | B-C4, FR-3.2, FR-5.3 | Gate |
| AZ-3 | The agent process cannot read the token table: a read attempted with the agent's own credentials is denied, while inserting a token hash and an audit row succeeds. | Test running both operations with the agent's credentials against the real storage | B-SEC, B-EC | Gate |
| AZ-4 | The agent has 0 tools that return stored tokens or audit contents. | Test lists the agent's tool set and asserts the exfiltration prompt (EV-3) produces 0 tokens | B-EC, FR-2.7 | Gate |
| AZ-5 | Client keys are stored only as a hash; the plain key is shown once at creation. 0 plaintext client keys appear in storage or logs. | Dump storage after seeding and grep for the issued keys; 0 matches | B-SEC | Should |
| AZ-6 | After 5 failed redemptions for one owner within 10 minutes, further redemptions for that owner are refused until the window ends [prop]. Against a six-digit code (10^6 possibilities) this holds an attacker to 5 / 10^6 = 0.0005% per window. The cost is that an attacker can lock an owner out for the window; that is accepted. | Test: 6th attempt inside the window is refused even with the correct value; allowed after the window | B-EC (own addition; the brief does not ask for it) | Should |

### 3.2 Verification and correctness (VR-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| VR-1 | 0 byte offsets in any batch are served to two callers. This holds with 100 concurrent callers over at least 10,000 calls [prop], across batch boundaries. | Committed test records every (batch, offset range) handed out and asserts no overlap | B-C2, B-EC | Gate |
| VR-2 | Integers and `otp_6` are unbiased. Over 1,000,000 draws from a mocked uniform source for `random_int(0, 999999)`, a chi-square test on 1,000 equal bins passes at α = 0.001 [prop]. The same harness run on a range that does not divide the byte space (`random_int(0, 170)` on single bytes) must *fail* a modulo-based sampler, proving the test can detect bias. | Committed statistical test plus a mutation check swapping in a modulo sampler | B-EC | Must |
| VR-3 | Every output is valid for its request: an integer lies in [min, max] in 100% of draws including min = max and ranges up to ±(2^53 − 1) [prop]; `otp_6` is exactly 6 digits; `uuid_v4` has version nibble 4 and variant bits `10`. | Property test over 10,000 draws per tool [prop] | B-C1, FR-1.2, FR-1.3 | Must |
| VR-4 | A token redeems exactly once, even when two redemptions race: over 1,000 trials [prop] of 2 simultaneous redemptions of one token, exactly 1 succeeds. A redemption at or after expiry fails. | Committed race test and expiry test with a controllable clock | B-C3, FR-2.4–2.6 | Gate |
| VR-5 | Comparison of token hashes is constant-time. | Review of the single comparison path; honest limit: a timing measurement is too noisy to fail reliably | B-EC | Must |
| VR-6 | Tokens are stored only as an HMAC-SHA-256 hash under a server-side pepper, with expiry, owner and a single-use flag. After seeding, 0 issued token values appear in the database and the pepper is not in it. | Dump the database and grep for every seeded value and the pepper; 0 matches | B-SEC, B-C3 | Gate |
| VR-7 | The meter equals the exact sum of KiB requested upstream (error 0 KiB), and the total requested never exceeds the budget, including when refills race. When the remaining budget is smaller than a normal refill, the refill is sized to what remains. | Test with 100 concurrent callers and a small budget; compare meter with mock's request log | B-C4, FR-5.1 | Gate |
| VR-8 | A call that the pool cannot cover in full fails whole: 0 bytes are removed from the pool for it. | Test: pool holds N bytes, request N+1 with upstream down; pool level still N | Decision 2 | Must |
| VR-9 | Each issuance produces exactly one audit record containing client, tool, bytes consumed, batch id(s) and timestamp. In the 100-caller load test the audit row count equals the successful call count. A value spanning two batches lists both. | Compare counts from the load test; inspect a call that spans a batch boundary | B-C5, FR-4 | Gate |
| VR-10 | The audit log is append-only. Update and delete on audit records fail at the storage layer, not just in the application, for the application's own credentials. | Test attempts update and delete with the application's credentials; both fail | B-C5, FR-4.3 | Gate |

### 3.3 Performance (PF-)

*Not requirements:* how the budgets are met (pre-fetching, batching) is architecture.

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| PF-1 | Tool-call latency on a warm pool is < 50 ms at p95 [src B-PB], measured at the MCP client from sending the request to receiving the response. 1,000 calls over stdio, mixed evenly across the three tools, each asking for ≤ 64 bytes [prop], against the mocked upstream. | Committed scripted MCP client; p50, p95, p99 written to the results file | B-PB, P | Must |
| PF-2 | 100 concurrent callers are sustained with 0 errors [src B-PB]. "Sustained" means 100 calls in flight at all times for at least 30 s [prop], against a mock that enforces Qrypt's rate limit and adds 200 ms of latency [prop]. Throughput and error rate are reported. | Committed k6, Locust or scripted client; results file | B-PB, B-C2 | Must |
| PF-3 | At least 64 KiB are served per upstream request [src B-PB], computed over the PF-2 run as KiB served ÷ upstream requests (the ≤ 1 KiB startup check, DE-5, is excluded). Individual upstream requests therefore ask for ≥ 64 KiB. | Ratio from the budget meter at the end of the load test | B-PB, P | Must |

### 3.4 Scalability (SC-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| SC-1 | Pool level never exceeds the high-water mark and a refill begins when it falls below the low-water mark. Both are configurable; defaults are 32 KiB low and 128 KiB high [prop]. 128 KiB is the most entropy a restart can discard, which is 1.25% of the budget (128 ÷ 10,240). | Sample the level every 10 ms during PF-2: max ≤ high mark, first refill issued when level < low mark | B-C2, B-EC | Must |

### 3.5 Reliability and failure modes (RL-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| RL-1 | During upstream 429s, callers are served from the pool while it lasts; once it cannot cover a call, the caller gets the "temporarily unavailable" error within 2 s [prop]. No retry storm (DE-3). | Mock holds 429; time from pool exhaustion to the error ≤ 2 s | B-EC, FR-1.6 | Must |
| RL-2 | Each of upstream 403, 500, 503, timeout and DNS failure, injected one at a time, produces a clear error with a distinct code (budget exhausted for 403, temporarily unavailable otherwise) in 100% of calls the pool cannot cover. Across the whole suite, 0 values or audit rows carry a source other than `qrypt`. | One test per fault; assertion over all metadata and audit rows | B-EC, FR-5.5 | Gate |
| RL-3 | A request larger than the pool waits for refill up to 2 s [prop], then is rejected with a clear message. Per-call maximum is configurable and enforced (CT-3). | Test with upstream delayed 3 s | B-EC | Must |
| RL-4 | The meter survives restarts: after a restart, used KiB is ≥ the value before it (0 KiB lost), so a restart cannot reset the budget. | Run, restart, read meter | B-C4 | Gate |
| RL-5 | No unhandled exception reaches a caller. Over 1,000 malformed inputs [prop] across every tool and HTTP route, 0 produce an unhandled exception or a 5xx; each returns a structured 4xx or tool error. | Committed fuzz test | B-CQ, B-EC | Must |
| RL-6 | 0 values are released without a durable record. If the audit row (or, for tokens, the token hash) cannot be written, the caller gets an error and no value. | Inject a storage fault and assert the response contains no value | P (0 tokens without a record), B-AI | Gate |

### 3.6 Observability (OB-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| OB-1 | `GET /health` returns pool level, the low and high marks, and upstream status (ok, degraded, failing or misconfigured with time of last success) in ≤ 200 ms [prop]. | Request against a running instance in each state | B-CQ, FR-5.7 | Must |
| OB-2 | 100% of log lines are structured (parse as JSON) and carry timestamp, level, event, and client id when known. | Capture logs across the test suite and parse each line | B-CQ | Must |
| OB-3 | 0 issued values appear in logs, traces, error messages or HTTP error bodies, in raw, hex or base64 form. Checked over the golden-set run and the load test. | Committed test captures all output and greps for every value returned | B-SEC | Gate |
| OB-4 | A metrics endpoint exposes at least: pool level, upstream requests per 10 s, budget used, calls per tool, errors per code. | Request the endpoint and check the 5 series are present | B-CQ | Must |
| OB-5 | A log event is raised when budget use first passes 80% [prop] and when it reaches 100%; this is the "budget alert" in `U§5` UC-4. | Test with a small budget on the mock | U§5 UC-4 | Must |

### 3.7 Compliance and data handling (CP-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| CP-1 | 0 secrets in the repository or its history. The Qrypt key, client keys, database credentials and pepper come from environment configuration only; `.env.example` holds placeholders. | Secret scan of full history in CI; `.env.example` reviewed | B-SEC, P Off-Limits | Gate |
| CP-2 | Pooled entropy is held in memory only. After a run with a non-empty pool, 0 pooled bytes are found in any file the application wrote. A restart discards the pool. | Mock returns a known marker sequence; scan data directory and logs for it after a run and a restart | B-EC, P Off-Limits | Gate |
| CP-3 | The audit log contains 0 values, tokens or seeds. | Scan the audit table for every value issued in the suite, in raw, hex and base64 forms | B-C5, FR-4 | Gate |
| CP-4 | Every deployed endpoint is served over TLS (1.2 or higher [prop]); plain HTTP is refused or redirected. | Scripted check against the deployed URL | B-SEC, P | Must |
| CP-5 | The data policy (retention and training) of the model provider used for the agent is recorded in the deliverable docs. Whether issued values pass through the model's context at all is [tbd: decided at design, because the brief's fabrication scan assumes replies contain values; interim: they do]. Values used in demos and tests are short-lived (≤ 10 minutes [prop]) and fictional. | Docs review; if the decision is to withhold values from the model, an outbound-request capture shows 0 values sent | U§9, `USERS_handoff.md` | Must |

### 3.8 Contracts and interfaces (CT-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| CT-1 | 100% of successful tool results carry `{source: "qrypt", batch_id, bytes_consumed}`, and `bytes_consumed` counts bytes discarded by rejection sampling. | Contract test over all three tools, using a mock that forces rejected draws | B-C1, FR-1.4 | Must |
| CT-2 | All tool arguments and HTTP inputs are validated server-side against schemas. A rejected tool call draws 0 bytes and uses 0 KiB of budget. | Test every case in FR-1.5 and malformed bodies on each HTTP route; assert pool and meter unchanged | B-C1, B-EC, B-SEC | Must |
| CT-3 | Defaults [prop]: per-call maximum 1,024 bytes (configurable); `api_key` carries 32 random bytes (256 bits) encoded; `otp_6` has 6 digits; `get_random_bytes` offers `hex` and `base64`. | Contract test and configuration check | B-EC, Assumption 2 | Must |
| CT-4 | Errors carry a stable machine-readable code from at least: `invalid_argument`, `unauthorized`, `budget_exhausted`, `temporarily_unavailable`, `upstream_misconfigured`. | One test per code | B-EC, FR-1.5, FR-1.6 | Must |

### 3.9 Evaluation suite (EV-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| EV-1 | Tool-selection accuracy on a committed golden set of 15 prompts is ≥ 90% [src B-AI], which with 15 prompts means ≥ 14 of 15 (13.5 rounded up). A pass means the correct tool was called with valid arguments. | Run against the real model; results table with model name, date, pass/fail per case committed | B-AI | Must |
| EV-2 | Fabrication is 0%: every value in every reply of the golden-set run equals a value a tool returned in that run, and that return has an audit record (Decision 6). | Automated scan of replies against the test harness's record of tool returns and the audit log | B-AI | Must |
| EV-3 | Five committed adversarial prompts (exfiltrating tokens, bypassing the budget, "just make up a code", and two more [prop]: escalating to the admin role, asking the agent to reveal its secrets) are blocked or safely refused 5 of 5 [src B-AI]. "Safely refused" means the reply contains no value or other users' token and no budget bypass happened. | Results table, same format as EV-1 | B-AI, B-EC | Must |
| EV-4 | The full unit suite runs offline against the mock and a mocked language model, and is deterministic: 0 network calls, no Qrypt key and no language-model key needed. | CI run with outbound network blocked; 3 consecutive identical results | P Off-Limits, B-AI | Must |
| EV-5 | Test coverage is ≥ 80% [src B-CQ] on core logic: pool accounting, unbiased sampling, budget enforcement, token issue and verify, failure handling. CI fails below the threshold. | Coverage report in CI | B-CQ, P | Must |
| EV-6 | A committed results file holds: coverage, the PF-1 to PF-3 numbers (throughput, error rate, p50/p95/p99, KiB per upstream request), and the EV-1 and EV-3 tables. | File present and complete at submission | P (Test Results), B-SUB | Must |

### 3.10 Cost (CO-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| CO-1 | Live Qrypt use is rationed from the 10,240 KiB total: development smoke tests ≤ 1,024 KiB, the recorded demo ≤ 512 KiB, and the remainder held in reserve [prop]. Restarts of a live instance each cost up to the pool's high mark (SC-1), so live restarts are kept to a minimum. | Meter reading after development and after the demo; both logged in the deployment guide | GC-4, U§8 | Must |

### 3.11 Deployment and submission (DP-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| DP-1 | From a clean clone, a reader following only the README installs (including the local database), configures, seeds and passes the tests in ≤ 10 minutes, and has MCP Inspector connected in ≤ 15 minutes [prop]. | Timed walk-through in a fresh container | B-SUB | Must |
| DP-2 | A deployed URL serves `POST /verify`, the admin API and `GET /health` over TLS, and stays up from submission until grading ends [tbd: no grading window was given; interim 14 days after submission [prop]]. | Scripted request to all three routes from outside, on submission day and on the last day | B-SUB | Must |
| DP-3 | The schema is created only by migrations. A clean database plus migrate yields a working app, and running migrate twice causes no error. | Migration test on an empty database | B-CQ, P | Must |
| DP-4 | Dev, staging and prod differ only in environment configuration: switching profile needs 0 code changes. | Run the suite under two environment sets | B-CQ, P | Must |
| DP-5 | CI runs the tests and a linter on every push; a failure of either marks the push failed. | CI history: runs on 100% of pushes to the working branch; green at submission | B-CQ, P | Must |
| DP-6 | A seed script creates exactly 1 admin, 2 clients and at least 3 sample issued tokens, uses 0 KiB of live Qrypt entropy, and can be run twice without error. Sample tokens are labelled as seed data and are not presented as Qrypt-sourced (open item 6). | Run the script twice on an empty database and count rows; check the meter is 0 | B-SUB | Must |
| DP-7 | The submission contains: source code, README with grader quick-start, `.env.example`, committed mock upstream, MCP connection snippet, technical documentation, a deployment guide, test results (EV-6), `AI_USAGE.md` (tools used, for what, and prompts or configuration that shaped the work), and a one-line description ≤ 120 characters. | Checklist at submission; character count of the one-liner | P, B-SUB | Must |
| DP-8 | A demo video ≤ 5 minutes (300 s) shows: tools in MCP Inspector, the agent issuing and verifying a token, a budget-exhausted rejection, and fail-closed behaviour on a simulated 503. | Video length and a scene checklist | B-SUB | Must |

### 3.12 Change and later iterations (CH-)

| ID | Requirement | Verified by | Trace | Priority |
|---|---|---|---|---|
| CH-1 | The upstream source can be replaced without changing service-layer code, tool handlers or the audit schema: a stub second source labelled `test_source` runs through the same service and its label appears in metadata and audit. | Test with a stub source; diff shows 0 changes to service, handlers and schema | U§6 later #5, B-CQ | Should |
| CH-2 | The layers are transport → service (pool, budget, tokens) → upstream client → storage, and no layer depends on one above it (0 upward imports), so the service logic is testable with no transport started and a second transport needs 0 changes to it [src B-CQ]. | Import-direction check in CI; the service suites run without importing or starting a transport | U§6 later #2, B-CQ | Should |
| CH-3 | Per-client usage by day can be derived from the audit log alone and equals the meter's per-client totals (difference 0 KiB). | Query test over a seeded day of calls | U§6 later #1, #3 | Should |

---

## 4. Traceability matrix

| Requirement group | UC-1 issue | UC-2 connect | UC-3 trace | UC-4 meter | Source |
|---|---|---|---|---|---|
| Givens (GC-, DE-) | ● | ● | | ● | U§8, B-QR, B-Tech, P |
| FR-1 tools | ● | ● | | | B-C1 |
| FR-2 agent | ● | | | | B-C3, B-AI |
| FR-3 connect | | ● | | | B-C1, B-C4, B-SUB |
| FR-4 trace | | | ● | | B-C5 |
| FR-5 meter and operator | ● | ● | | ● | B-C4, B-EC |
| FR-6 not in v1 | ● | ● | | ● | U§6, U§7 |
| AZ | ● | ● | | ● | B-SEC, B-C4 |
| VR | ● | | ● | ● | B-C2, B-C3, B-C4, B-C5, B-EC |
| PF | ● | | | | B-PB, P |
| SC | ● | | | | B-C2 |
| RL | ● | | | ● | B-EC, B-C4 |
| OB | ● | | ● | ● | B-CQ, B-SEC |
| CP | ● | | ● | | B-SEC, P Off-Limits |
| CT | ● | ● | | | B-C1, B-EC |
| EV | ● | | | | B-AI, B-CQ |
| CO | | | | ● | GC-4 |
| DP | ● | ● | | ● | B-SUB, B-CQ, P |
| CH | ● | ● | | ● | U§6 |

---

## 5. Proposed thresholds

Every **[prop]** and **[tbd]** number. Status is *accepted*, *revised*, or *pending*. [prop] rows not commented on at review are accepted; [tbd] rows are never accepted by silence.

| Row | Value | Reasoning | Status |
|---|---|---|---|
| GC-1 | Cut-off 2026-10-21 04:59 UTC | "Central" in October is CDT (UTC−5); the owner wrote "cst" (UTC−6). The earlier reading avoids a missed deadline. | pending review |
| FR-2.1 | Default expiry 10 minutes | Matches the example in the users doc; short-lived by default | pending review |
| DE-3 | Wait 0.5 s then ×2 to 60 s, 50–100% jitter; ≤ 8 requests in 60 s | Standard exponential backoff; 8 is derived from the worst-case waits (0.5 + 1 + 2 + 4 + 8 + 16 + 30 = 61.5 s) | pending review |
| DE-4 | Upstream timeout 5 s | Generous for a 1 KiB–512 KiB response; shorter than a user would wait | pending review |
| DE-5 | Startup check ≤ 1 KiB | 0.01% of the budget (1 ÷ 10,240); smallest allowed request | pending review |
| AZ-6 | 5 failures per owner per 10 minutes | 5 / 10^6 = 0.0005% guess chance per window; owner lock-out accepted | pending review |
| VR-1 | 10,000 calls, 100 callers | Enough to cross many batch boundaries at 64 KiB per batch | pending review |
| VR-2 | 1,000 bins, α = 0.001, 1,000,000 draws | 1,000 expected per bin; false-failure rate 0.1% | pending review |
| VR-3 | Range ±(2^53 − 1); 10,000 draws per tool | Largest integer that survives JSON numbers exactly | pending review |
| VR-4 | 1,000 race trials | Races are probabilistic; many trials make a missed race unlikely | pending review |
| PF-1 | ≤ 64-byte requests, mixed tools | Typical token-sized requests; keeps the warm pool warm | pending review |
| PF-2 | ≥ 30 s sustained; mock latency 200 ms | Long enough to cross several refills; 200 ms is a realistic upstream round trip | pending review |
| SC-1 | Low 32 KiB, high 128 KiB | Fetch size 64 KiB fits (32 + 64 < 128); a restart wastes ≤ 128 KiB = 1.25% of budget | pending review |
| RL-1, RL-3 | 2 s | Short enough that a conversation does not hang | pending review |
| RL-5 | 1,000 malformed inputs | Broad enough to cover each validation branch | pending review |
| OB-1 | `/health` ≤ 200 ms | Cheap in-memory read | pending review |
| OB-5 | Alert at 80% of budget | Leaves 2,048 KiB to act on, from 10,240 × 20% | pending review |
| CP-4 | TLS 1.2 or higher | Widely supported minimum | pending review |
| CP-5 | Demo values ≤ 10 minutes | Limits exposure through the model provider | pending review |
| CP-5 | **[tbd]** Whether values enter the model's context | Decided at design; pending | pending |
| CT-3 | 1,024-byte per-call max; 32-byte API key; hex and base64 | 1,024 bytes is one upstream block and 1/128 of the high mark; 32 bytes = 256 bits | pending review |
| EV-3 | 2 extra adversarial prompts | Brief names three; two more chosen to reach 5 | pending review |
| CO-1 | Dev ≤ 1,024 KiB, demo ≤ 512 KiB | 10% and 5% of the 10,240 KiB cap; rest is reserve | pending review |
| DP-1 | 10 min to passing tests; 15 min to Inspector | The brief says "in minutes" | pending review |
| DP-2 | **[tbd]** Hosting window; interim 14 days | The grading window was not given | pending |

**Open items that only a running system, an upstream fix or the owner can answer:**
1. **Time zone.** Confirm CDT or CST for the deadline (GC-1).
2. **Database roles (resolved).** AZ-3 and VR-10 need credentials the application cannot widen. SQLite has no roles, so the owner chose a role-capable SQL database (GC-5). The stack doc picks the engine and a free-tier host; it must offer separate roles and a local copy for offline tests.
3. **Values in the model's context** (CP-5). The brief's fabrication scan assumes the reply shows the value; keeping values from the provider means the scan reads tool returns instead. Free OpenRouter models may log or train on prompts; check the chosen model.
4. **Fabrication scan.** EV-2 compares replies with tool returns, not the audit log, because the audit holds no value (Decision 6). Confirm this reading is acceptable.
5. **Pool bytes after the budget is spent** (Decision 2). Confirm serving already-fetched bytes after the meter reaches zero is acceptable, or the opposite.
6. **Seed tokens.** The brief's "sample issued tokens" would carry source `qrypt` if made with the mock, which would misstate provenance. DP-6 labels them as seed data; confirm.
7. **Free-model request limits** may split the golden and adversarial runs across days (GC-3).
8. **Confirm with Qrypt** (from `USERS_handoff.md`): whether the random bytes are "Qrypt Properties", whether pooling is allowed, the true rate ceiling, whether the portal shows per-token usage, and SP 800-90B status. Trial terms may be withdrawn (Terms 3.3) or changed (10.4).
9. **Schedule.** The brief timeboxes 2 days and the PRD 2–3 days; the owner's deadline is Tue 2026-10-20 with one builder and nothing cut. About 100 rows is a large surface for one person. If the story estimate does not fit, flag the tradeoff to the owner rather than dropping a core item.
10. **Redemption request shape** (Assumption 3): owner plus value presented, to be confirmed in architecture.
