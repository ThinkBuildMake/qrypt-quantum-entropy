# Limitations

## Limitations

What this system does not do, does not prove, or has not been shown to do. Read this before relying on any guarantee in
the other documents.

## The reply scan is a check, not a proof

The agent's reply check (`agent/reply_check.py`) removes value-shaped strings that no tool returned in the conversation:
a UUID, a run of 32 or more hex digits, a run of six or more digits, and a 20 or more character base64-like string
holding a letter and a digit. It is a pattern check on text, and a determined or unlucky model can get a value past it.
It does not find:

- a short number (a `random_int` result of one to five digits);
- a value split by spaces, commas or dashes ("731 905"), spelled out in words, reversed, or encoded;
- a value run into letters ("code731905x");
- a value inside an image or a link.

It also removes ordinary long numbers (a phone number, a timestamp), which errs on the safe side. The second line of
defence is redemption: a code the product never issued comes back `unknown` from `POST /verify`. The golden-set and
adversarial evaluation that measures the scan (EV-1 to EV-3, S40) is part of this build (`make eval-golden`,
`make eval-adversarial`, `make eval-summary`). Without an OpenRouter key it runs against a scripted stub that only
proves the harness works; the real-model numbers are `[unverified]` until a keyed run.

## Trust boundary of the issuing role

The MCP server runs on the caller's machine with the `mcp_server` database role. Whoever controls that process can insert
audit rows for any client, raise `budget.used_kib`, write token hashes and read credential hashes. The database stops
that role reading or changing tokens and audit rows already written, and stops it lowering the meter, but it cannot
stop it lying about new rows. The audit therefore proves what a well-behaved server did; it does not prove that a
compromised one did nothing else. This is accepted for the assessment. Related: the hosted service uses the least-privilege
`http_service` role only if its `DATABASE_URL` is set to that role's connection string, which the shipped `render.yaml`
does not do `[unverified]`; a database owner or superuser can bypass the guard triggers.

## One live process per Qrypt key

The 30-requests-per-10-seconds limiter lives in the MCP server process (DE-1). Qrypt's limit is per account. Two live
MCP servers on one key would each refill on their own and could exceed it, and need a shared limiter, which is not built.
The budget meter is shared through the database, so two processes cannot exceed the cap (`make two-process-budget`), but
they can trip Qrypt's 429. Each restart discards the pool (up to 128 KiB reserved and never served), and reserved KiB
are not refunded, so restart live servers as little as possible.

## 30-day database life

The free Render database expires 30 days after creation and is deleted 14 days later; there are no platform backups. The
production database is to be created on or after 2026-10-20 so it outlives the 14-day window after submission. After
that date everything in it, including the audit trail, is gone unless a backup artifact (kept 30 days) is restored.
Up to 24 hours of writes can be lost between daily backups. The free web service sleeps after 15 minutes idle and takes
about a minute to wake. None of this was observed on Render `[unverified]`. Each database has its own meter, so a
replacement database restarts at 0 KiB while the Qrypt allotment (10,240 KiB total) does not.

## Not built in v1

Deliberately out of this version (FR-6.2):

- quotas per client: there is one global budget and per-client usage is only reported;
- HTTP MCP: the tools are reachable over stdio only, which also keeps entropy off the hosted service (GC-7);
- an admin dashboard: admins use the HTTP routes, `make report` and `/metrics`;
- a second client: there is one agent, and no second-language client;
- a second entropy source in production: the `Source` protocol is swappable and a deterministic `test_source` exists for
  tests, but no fallback to the operating system's generator exists, by design.

## Other limits worth knowing

- **Never run live.** No call to the real Qrypt service, OpenRouter, Render, Neon or GitHub Actions was made. The mock
  enforces the documented limits, not necessarily the real ones. Latency and load figures in `results/` come from a
  local Postgres 16 and the mock with 200 ms added latency.
- **Postgres 18 untested.** The tests ran on 16.
- **Redemption lock-out.** Failed redemptions are limited per owner (five in 10 minutes), not per caller, so anyone holding
  a client key who knows an owner id can keep that owner locked out (`docs/DEVIATIONS.md` 13).
- **Metrics are per process and reported every 5 s.** They are as fresh as the last report and read "not reporting"
  after 15 s.
- **A multi-draw call discards bytes** if a later draw fails, so a failed `random_int` can spend entropy without issuing.
- **Trace-lookup limit is per process.** Failed `/admin/trace-token` lookups are counted in the HTTP process's memory,
  so it restarts with the process and is not shared between instances (`docs/DEVIATIONS.md` H03-2).
- **A failed refill is charged.** The budget meter is not refunded for batches that failed, even when no request
  reached Qrypt; the pool only limits how often it tries (`docs/DEVIATIONS.md` H03-1).
- **Not fixed (review items 7 and 11).** Skipped on purpose in the H03 pass; they are known and unchanged.
- **Seed tokens have no audit record** (they are flagged `is_seed`).
- **The 15-prompt golden set, the adversarial set and a real-model trial** were not run against a real model here.
