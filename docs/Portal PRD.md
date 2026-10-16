PRD

AI agents need a governed, auditable source of high-quality randomness for generating API keys, OTPs, session identifiers, and seeds, rather than relying on LLM-fabricated or ad-hoc random values. Build an MCP server exposing Qrypt quantum entropy as typed tools, plus a Secure Token Agent app that uses those tools to issue, store, and verify tokens, ensuring every random value is traceable to a real entropy source, never fabricated, and never reused.

Problem & Context

Business Context

Security reviews increasingly scrutinize the provenance of randomness in AI-driven systems. LLM-invented 'random' values are guessable and unauditable, while unmanaged entropy services risk rate-limiting or exhaustion. A governed entropy gateway with quotas, provenance tracking, and graceful failure handling enables teams to pass security reviews and safely ship agent features touching credentials.

Impact Metrics

Cut randomness/token-generation portion of security review from ~8 hours to ~1 hour per review (assumption: 6 reviews/year × 7 hrs × $90/hr = $3,780/year savings); eliminate LLM-fabricated tokens entirely (target: 0 issued tokens without matching entropy record); serve ≥ 64 KiB of tool output per upstream Qrypt request via pooling.

Requirements & Success Criteria

Functional Requirements

1) Entropy MCP tools: get_random_bytes(length, encoding), random_int(min, max), generate_token(kind) with metadata {source, batch_id, bytes_consumed}; 2) Entropy pool with backpressure: prefetching, single-use byte serving, low/high-water marks, max 30 upstream requests/10s for 100 concurrent calls; 3) Secure Token Agent CLI: natural-language token issuance, hashed storage with expiry/owner, single-use /verify endpoint; 4) Client keys and budget meter: per-client auth, global KiB budget tracking (default 10,240 KiB), fail-closed on exhaustion, admin usage visibility; 5) Provenance and audit log: append-only record of client, tool, bytes consumed, batch id, timestamp (never the random value or token).

Performance Benchmarks

Tool call latency (warm pool) < 50ms p95 over 1,000 calls against mocked upstream; 100 concurrent tool calls sustained with 0 errors (load tested via k6/Locust/scripted client); upstream efficiency ≥ 64 KiB served per upstream request.

Code Quality Expectations

Test coverage ≥ 80% on core logic (pool accounting, unbiased sampling, budget enforcement, token issue/verify, failure handling); clear layered architecture (transport → service layer → upstream client → storage) with swappable/mockable upstream client; graceful degradation per edge cases with no unhandled exceptions; structured logs without secrets/random values; GET /health endpoint with pool level and upstream status; database migrations; CI runs tests and linter on every push.

Time Constraints

2-3 days

Technology

Required Languages

Python, TypeScript/Node, Go, Rust, or C++ (choose one)

AI / ML Frameworks

Any official MCP SDK; any LLM supporting tool calling (free tier or local model required for grading)

Dev Tools

Git, a test framework, MCP Inspector (npx @modelcontextprotocol/inspector), k6/Locust or scripted client for load testing, CI with linter

Cloud Platforms

Any free-tier host for the HTTP API

Other Requirements

SQLite or any SQL database with free tier; database migrations; environment-based configuration (dev/staging/prod); TLS for deployed endpoints; HMAC-SHA-256 token hashing with server-side pepper

Off-Limits Tech

No paid API key required for grading; test suite must run against a mock, not the live Qrypt API; live usage capped at ≤10MB (10,240 KiB) during assessment; never commit QRYPT_API_KEY; pooled entropy held in memory only, never written to disk in plaintext; random values/tokens never logged.

Submission & AI Policy

AI Usage Documentation

Required

Required Deliverables

    Source Code
    Technical Documentation
    Demo Video
    Deployment Guide
    Test Results
    AI Usage Log