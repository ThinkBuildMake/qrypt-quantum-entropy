# STACK.md — Quantum Entropy MCP Server & Secure Token Agent technology choices

**What this document is.** A comparison of candidate technologies for each module, and the choice for each. `REQUIREMENTS.md` defines what the system must do; requirement IDs (e.g. `GC-5`) appear here only as the reason a choice wins or loses. Which components exist and the load they carry are in `HIGH_LEVEL_DESIGN.md`; how the pieces fit together is designed in `ARCHITECTURE.md`.

**How the shortlist was built.** Each module's options were researched against `REQUIREMENTS.md`, `HIGH_LEVEL_DESIGN.md`, `USERS.md` and the assessment brief, with the deciding facts verified on 2026-10-17. A separate agent that hadn't seen the research then checked the chosen stack for conflicts. It changed four things: backups are encrypted before upload because the repository is public; the backup job and its key are added as parts the high-level design left out; issued values should stay out of the model's context (a proposal, not yet decided); and a spike on database roles is made a go-live prerequisite. The owner then accepted the free host's cold start for the demo, so no keep-alive is used.

**Markers.** **[verify]** = an external fact checked on 2026-10-17 that should be re-confirmed before it's relied on. Go-live prerequisites, open choices and open questions are in the last sections.

---

## Summary

| # | Module | Choice | Main reason |
|---|---|---|---|
| 1 | Language | Python 3.14 | Fastest for one builder (GC-1, GC-5) |
| 2 | MCP SDK | `mcp` 2.3.0, `MCPServer`, stdio | Official SDK, stable line (GC-6) |
| 3 | HTTP service | FastAPI on uvicorn | Schema validation on every route (CT-2) |
| 4 | Database | Render Postgres Free, Postgres 18 | Same vendor as the host; roles to verify (GC-5) |
| 5 | Driver and migrations | psycopg 3 with a pool; Alembic with raw SQL | Roles live in migrations (DP-3) |
| 6 | Hosting | Render free web service | Free tier, TLS (GC-9, CP-4) |
| 7 | Backups | Scheduled `pg_dump`, encrypted, via GitHub Actions | Render Free has none (RL-7) |
| 8 | Upstream client | httpx | Timeouts, swappable transport (DE-4, CH-1) |
| 9 | Hashing | Python standard library | HMAC-SHA-256 and constant-time compare (VR-5, VR-6) |
| 10 | Agent | `openai` SDK to OpenRouter, hand-written tool loop | Few hidden calls against a 50-a-day cap (GC-3) |
| 11 | Agent model | A `:free` tool-calling model; local Ollama fallback | $0 (GC-2, GC-3) |
| 12 | Logs and metrics | structlog; prometheus_client | JSON logs, six series (OB-2, OB-4) |
| 13 | Tests | pytest, pytest-cov, hypothesis, scipy | Statistical and property tests (VR-2, VR-3, EV-5) |
| 14 | Load test | Scripted asyncio client using the MCP client | PF-2 is over stdio; k6 and Locust speak HTTP |
| 15 | Local database | Postgres 18 in Docker Compose | Real roles offline (AZ-3, VR-10, EV-4) |
| 16 | Packaging | uv with a lockfile | Clean clone in ≤ 10 min (DP-1) |
| 17 | CI | GitHub Actions, ruff, gitleaks CLI | DP-5, CP-1 |
| 18 | Secrets | Environment variables via pydantic-settings | CP-1 |

**Cost (total, $0 budget, GC-2):**

| Item | Cost |
|---|---|
| Render free web service and free Postgres | $0 [verify] |
| GitHub Actions on a public repository | $0 [verify] |
| OpenRouter `:free` model | $0 |
| Qrypt, within the trial allowance | $0 |
| **Typical** | **$0** |
| **Worst case** | **$0 while no payment method is added.** Render suspends free services when limits run out; it doesn't bill. Upgrading an expired database, or buying OpenRouter credit, would be paid spend and breaks GC-2. |

---

## Glossary

Terms used in this document that someone new to the project wouldn't know yet. `REQUIREMENTS.md` defines the product's own vocabulary — value, token, issuance, batch, pool, budget, fail closed, fabrication — and isn't repeated.

**Database**

| Term | Meaning |
|---|---|
| Role (database) | A named login inside the database with its own list of allowed actions, such as "may add rows to this table but may not read it." |
| Migration | A small, ordered script that builds or changes the database's tables. Running all of them on an empty database produces the working layout. |
| `pg_dump` | A tool that copies the whole content of a Postgres database into one file. |
| Scale to zero / spin-down | A host shutting a service down after it has been idle, then starting it again on the next request, which takes a while. |

**Services and tooling**

| Term | Meaning |
|---|---|
| `:free` model | A model on OpenRouter that costs nothing to call but is limited to a few requests a minute and a few dozen a day. |
| Tool calling | A language model's ability to answer with "call this function with these arguments" instead of text. |
| Lockfile | A file recording the exact version of every library, so every machine installs the same ones. |
| Docker Compose | A tool that starts programs, here a database, from a short text description on any machine with Docker installed. |
| `age` | A small file-encryption tool. Anyone with the public key can encrypt a file; only the holder of the private key can open it. |
| gitleaks | A scanner that looks through files and history for passwords and keys. |

**Testing**

| Term | Meaning |
|---|---|
| Chi-square test | A statistical check of whether numbers are spread evenly or favour some values. |
| Property-based test | A test that tries thousands of generated inputs against a rule that must always hold, instead of a few hand-picked examples. |
| Histogram (metrics) | A count of how many measurements fell into each size band, from which medians and 95th percentiles can be read. |

---

## 1. Language

| Option | Pros | Cons |
|---|---|---|
| **Python 3.14** ✅ | Official MCP SDK; fastest to write and test for one builder (GC-1); strongest statistics and property-test tooling (VR-2, VR-3); supported to 2030-10 [verify] | Slower than Go or Rust in raw speed; the upstream limit of 3 requests per second, not the language, caps throughput (HLD §2) |
| Python 3.13 | Works with everything below | Leaves bugfix support on 2026-10-01 [verify] |
| TypeScript/Node | Official SDK; matches MCP Inspector | Slower statistics test setup; no gain over Python for this load |
| Go | Strong concurrency | More code per feature; one builder, three days (GC-1) |
| Rust or C++ | Fastest | Cannot finish all five core items in the time (GC-1) |

**Choice: Python 3.14.** Allowed by GC-5, and the only option whose cost fits GC-1. Python 3.13 is the fallback if a library has no 3.14 build.

## 2. MCP SDK

| Option | Pros | Cons |
|---|---|---|
| **`mcp` 2.3.0, `MCPServer` class** ✅ | Stable line since 2026-07-28; the SDK's `Client` can launch the server over stdio, which the agent and load test both need (GC-6) [verify] | 15 days old at selection; replaces v1's `FastMCP` and `ClientSession`, so older examples don't match; pulls in its own second HTTP library |
| `mcp` 1.30 (`FastMCP`) | Maintained; most online examples match | Maintenance-only line |

**Choice: `mcp` 2.3.0, pinned `>=2.3,<3`.** GC-6 requires an official SDK; the current stable line is the better base for a new project.

**Needs from the design.** The server's database credential is passed only to the server's own process, never to the agent process (AZ-3).

## 3. HTTP service

| Option | Pros | Cons |
|---|---|---|
| **FastAPI on uvicorn** ✅ | Request validation from typed models on every route and an error body that is easy to keep structured (CT-2, RL-5); same stack as the SDK's server pieces | Pins a web-framework version that must agree with the SDK's; the resolver must be checked |
| Flask | Small | Validation by hand (CT-2) |
| Starlette alone | Fewest dependencies | Validation by hand (CT-2) |

**Choice: FastAPI.** CT-2 asks for schema validation on every input; FastAPI does it without extra code.

## 4. Database

**Why it exists.** Holds every "never lose" record and the shared budget meter (HLD §3). Separate roles are required by GC-5 so the database, not the application, enforces AZ-3 and VR-10.

| Option | Pros | Cons |
|---|---|---|
| **Render Postgres Free, Postgres 18** ✅ | One vendor and one sign-up with the host; the hosted service reaches it over the internal network; no idle spin-down described; external connections allowed over required TLS [verify] | Expires 30 days after creation, then deleted 14 days later; no backups; 1 GB; may restart at any time; no pooler; **whether extra roles can be created is not documented** (spike S-1) |
| Neon Free | Roles documented; no expiry; no card | Scales to zero after 5 min idle; 100 compute-hours a month, and running out suspends it [verify]; no scheduled backups; a second vendor |
| Supabase Free | Postgres with auth | Pauses after about 7 idle days; 500 MB [verify] |
| SQLite | No setup | Excluded by GC-5 (no roles) |

**Choice: Render Postgres Free.** Meets GC-5 and GC-9 at $0 with the fewest vendors; the 30-day expiry is acceptable for an assessment (`DP-2`'s interim window is 14 days). Neon is the fallback if spike S-1 fails.

**Needs from the design.**
- The table owner must be a separate migration role, because append-only (VR-10) fails if the application role owns the table.
- An insert-only role can't use `INSERT … RETURNING` or `ON CONFLICT`, since both need read access (AZ-3).
- Role passwords come from the environment, not from migration files (CP-1).

## 5. Driver and migrations

| Option | Pros | Cons |
|---|---|---|
| **psycopg 3 with `psycopg_pool`; Alembic with raw SQL migrations** ✅ | Supports Python 3.14 and Postgres 18 [verify]; migrations can hold `CREATE ROLE` and `GRANT` (DP-3, GC-5); running migrate twice is safe | Alembic needs SQLAlchemy as a dependency used only for migrations |
| asyncpg, plain SQL runner | Fewer dependencies | Hand-built migration tracking (DP-3) |
| SQLAlchemy ORM throughout | Familiar | Hides the exact conditional updates the meter needs (VR-7) |

**Choice: psycopg 3 and Alembic.** DP-3 asks for migrations; Alembic is the common tool that carries role and grant statements as SQL.

**Needs from the design.** The pool must reconnect after a database restart, because Render may restart a free database at any time (RL-7).

## 6. Hosting

| Option | Pros | Cons |
|---|---|---|
| **Render free web service** ✅ | Free; no payment method stated as required; managed TLS; Python 3.14 and uv supported [verify] | Sleeps after 15 min idle and takes about 1 min to wake (accepted for the demo; OB-1, PF-5 and DP-2 are stated for an awake service); may restart at any time |
| Google Cloud Run | Generous free quota | Needs a billing account and card (GC-2) |
| Koyeb | Free instance | Card pre-authorisation reported [verify] |
| Fly.io, Railway | Pleasant to use | No longer free for new accounts [verify] |

**Choice: Render free web service.** Only option that is free without a card (GC-9, GC-2).

## 7. Backups

**Why it exists.** RL-7 accepts a daily backup, and Render Free offers none.

| Option | Pros | Cons |
|---|---|---|
| **Scheduled GitHub Actions job running `pg_dump` in a Postgres 18 container, encrypted with `age`, uploaded as an artifact** ✅ | No extra vendor; restore tested against the local Postgres 18 (RL-7) | The repository is public, so the file must be encrypted; the job needs a read-only backup role; a dump doesn't carry roles, so the role migrations are re-run after a restore |
| `pg_dump` run by hand from the laptop | No secrets in GitHub | Not scheduled; easy to forget |
| Upgrade the database to a paid plan | Built-in backups | Paid (GC-2) |

**Choice: scheduled, encrypted `pg_dump`.** Meets RL-7 at $0; the dump holds client-key hashes, token hashes and audit rows (CP-1, CP-3).

**Needs from the design.** The dump's contents are limited to hashes and metadata and the pepper is never in the database (VR-6). Neither the connection string nor the dump is printed in job logs.

## 8. Upstream client

| Option | Pros | Cons |
|---|---|---|
| **httpx** ✅ | Per-request timeout (DE-4); a mock plugs in at the transport, so the real client code runs in the offline tests (EV-4) | The MCP SDK installs a second HTTP library alongside it |
| requests | Familiar | Not async; no transport hook |
| aiohttp | Async | Mocking is heavier |

**Choice: httpx.** DE-4 and CH-1 need a timeout and a swappable source.

## 9. Hashing

**Choice: the Python standard library** (`hmac`, `hashlib`, `secrets`). HMAC-SHA-256 with a server-side pepper and a constant-time compare (VR-5, VR-6) need no outside package.

## 10. Agent

| Option | Pros | Cons |
|---|---|---|
| **`openai` SDK pointed at OpenRouter, hand-written tool loop, the MCP `Client` for tools** ✅ | OpenRouter speaks the same protocol; no hidden retries or stored conversation state against a 50-requests-a-day cap (GC-3); the reply check (OB-7) sits in plain code | We write the translation between MCP tool descriptions and the model's tool format |
| PydanticAI, LangChain | Less glue | Hidden extra model calls and retries; state kept between turns; harder to count requests (GC-3) |

**Choice: the `openai` SDK with a hand-written loop.** Every model request is visible and counted (GC-3, OB-7).

## 11. Agent model

**Why it exists.** GC-3 requires OpenRouter and a free path.

| Option | Pros | Cons |
|---|---|---|
| **A `:free` tool-calling model**, candidates `google/gemma-4-31b-it`, `poolside/laguna-s-2.1`, `nvidia/nemotron-3-super-120b-a12b` ✅ | $0; listed with tool support on 2026-10-17 [verify] | 20 requests a minute and 50 a day until $10 of credit is bought; each runs on a single provider; some free providers may use prompts for training [verify] |
| Local model through Ollama | No daily cap; no data leaves the machine | Needs a capable computer; slower; accuracy unknown |
| Paid model | Reliable | Paid (GC-2) |

**Choice: a `:free` model, picked by a trial run against the golden set (EV-1), with Ollama as the fallback.** The model name goes in the results file (GC-3).

**Needs from the design.** The 15-prompt golden set, 5 adversarial prompts and the PF-4 timing run together take roughly 60 to 150 requests, more than one day's cap (open item 7), so runs are spread over days and requests counted. Whether issued values pass through the model is open question 2.

## 12. Logs and metrics

| Option | Pros | Cons |
|---|---|---|
| **structlog (JSON) and `prometheus_client`** ✅ | Every line parses as JSON with timestamp, level, event and client id (OB-2); counters and histograms give the six series (OB-4) | Metrics live per process; histogram percentiles are estimates |
| Standard-library logging with a JSON formatter | No dependency | More code for context fields |

**Choice: structlog and `prometheus_client`.**

**Needs from the design.** Pool level, upstream request rate, calls per tool and tool-call duration live in the MCP server process, but `/metrics` is served by the HTTP service. Where those numbers cross is open question 1.

## 13. Tests

**Choice: pytest, pytest-asyncio, pytest-cov, hypothesis, scipy.** pytest-cov enforces 80% in CI (EV-5); hypothesis drives VR-3's property tests; scipy supplies the chi-square test for VR-2. Wheels for Python 3.14 are not checked for all of them; `uv sync` in CI is the check.

## 14. Load test

| Option | Pros | Cons |
|---|---|---|
| **Scripted asyncio client using the MCP `Client`** ✅ | Drives the real stdio tools with 100 calls in flight (PF-2); PF-2 allows a scripted client | We write and maintain it |
| Locust, k6 | Ready-made reports | They speak HTTP, not MCP over stdio |

**Choice: a scripted asyncio client.** The tool path under test is stdio (GC-6), which neither Locust nor k6 drives.

## 15. Local database

| Option | Pros | Cons |
|---|---|---|
| **Postgres 18 in Docker Compose** ✅ | Same major version as the host; real roles for the offline AZ-3 and VR-10 tests (EV-4) | Graders need Docker installed (DP-1) |
| `pgserver` (Postgres from pip) | No Docker | Smaller project; role support unchecked [verify] |
| testcontainers | Per-test databases | Also needs Docker |

**Choice: Docker Compose.** Keeps the local engine equal to the hosted one (GC-5).
## 16. Packaging

**Choice: uv with a committed lockfile.** One command installs the exact libraries (DP-1); Render supports uv with a lockfile. `uv lock` is run on a blank project first to catch version clashes among the SDK, FastAPI and the model client.

## 17. CI

| Option | Pros | Cons |
|---|---|---|
| **GitHub Actions with a Postgres service, ruff, and the gitleaks command-line tool** ✅ | Free for public repositories [verify]; runs tests, lint and secret scan on every push (DP-5, CP-1) | Standard runners may ship an older `pg_dump` than 18 (see 7) |
| gitleaks GitHub Action | Less setup | Its licence terms are unchecked [verify] |

**Choice: GitHub Actions with the gitleaks command-line tool (MIT).**

## 18. Secrets

**Choice: environment variables read through pydantic-settings, with `.env.example` committed and Render's environment store for the hosted service.** CP-1 asks for nothing else.

---

## 19. Go-live prerequisites

| Prerequisite | Lead time | Blocks |
|---|---|---|
| **Spike S-1: on a real Render free database, run `CREATE ROLE … LOGIN`, `GRANT`, `REVOKE`, log in as each role, and an `INSERT` without `RETURNING`** | Minutes | Choice 4; AZ-3, VR-10. If it fails, fall back to Neon |
| Create the production database no earlier than 2026-10-20 (expires 30 days after creation, one free database per workspace). Delete the spike database first | Same day | DP-2 |
| `uv lock` and `uv sync` on Python 3.14 in a blank project | Minutes | Whole stack |
| Backup encryption key pair made; private key kept offline | Minutes | RL-7 |
| Spread golden-set, adversarial and PF-4 runs over 2–3 days starting now | 2–3 days | EV-1, EV-3, PF-4 (deadline 2026-10-20) |
| Account-level opt-out of training on prompts for free models set in OpenRouter | Minutes | CP-5 |

## 20. Open choices

| Choice | Current lean | Settled by |
|---|---|---|
| Which `:free` model | `google/gemma-4-31b-it` | Trial against the golden set, and that its tool calls parse |
| Migrations tool | Alembic | A plain-SQL runner if the extra dependency is unwanted |

## 21. Open questions

Things no technology choice can settle. Each goes to the owner of the named document.

| # | Kind | Issue | Proposed change |
|---|---|---|---|
| 1 | Design | **Metrics gap.** OB-4's pool and upstream series live in the MCP server process, which the HTTP service can't read | Architecture decides: the server writes its counters into the status report, or OB-4 is met by the local process only |
| 2 | Requirements | **CP-5 and free-model privacy.** Issued values entering the model's context send OTPs and keys to free providers | Decide now to keep values out of the model's context (the agent code inserts them into the reply); OB-7 then reads tool returns |
| 3 | Design | **HLD omissions.** It says "Scheduler: none" and backups are "provided by platform" | Add to §5 and §6: a daily encrypted backup job and its key; no platform backups on the free database |
| 4 | Requirements | **AZ-3 wording** assumes the agent holds an insert-only credential; the design has the server write the hash | Already open item 11; reword AZ-3 at architecture |
| 5 | Requirements | **EV-1, EV-3, PF-4 against a 50-a-day cap** and a 2026-10-20 deadline | Open item 7; plan eval runs now, counting requests |

---

## Sources (checked 2026-10-17)

- [Render: Deploy for free](https://render.com/docs/free)
- [Render Postgres: creating and connecting](https://render.com/docs/postgresql-creating-connecting)
- [Render Postgres: backups](https://render.com/docs/postgresql-backups)
- [Render Postgres: credentials](https://render.com/docs/postgresql-credentials)
- [Neon: plans](https://neon.com/docs/introduction/plans)
- [Neon: roles](https://neon.com/docs/manage/roles)
- [OpenRouter: limits](https://openrouter.ai/docs/api-reference/limits)
- [OpenRouter: privacy and logging](https://openrouter.ai/docs/features/privacy-and-logging)
- [OpenRouter: models list](https://openrouter.ai/api/v1/models)
- [mcp on PyPI](https://pypi.org/project/mcp/)
- [MCP Python SDK: what's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [MCP Python SDK: stdio client](https://py.sdk.modelcontextprotocol.io/client/transports/)
- [Python lifecycle dates](https://endoflife.date/python)
- [Qrypt assessment brief](Qrypt%20-%20Quantum%20Entropy%20MCP%20Server.md)
