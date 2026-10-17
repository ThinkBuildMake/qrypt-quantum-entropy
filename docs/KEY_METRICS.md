# KEY_METRICS.md — Quantum Entropy MCP Server & Secure Token Agent

**Status:** draft. The success sentence below is a proposal and waits for the owner's agreement.

Every number is computed by the system from records it already keeps (logs, audit rows, the meter, request timestamps). Nothing here needs a person to judge a case. This is an assessment build, so the numbers are read from test, load and demo runs and written to the results file (EV-6); the same definitions hold if it ever runs for real.

## What success means

Language-model "random" values are guessable: in one test, 50 requested passwords gave only 30 unique values, and one string appeared 18 times (`USERS.md` §2). Priya's agent must never hand out a value the model made up, and she must be able to say where each one came from (`USERS.md` §3).

Success means every code the agent shows an end user was issued by the product, and every one of them can be traced to a batch.

## Success metric: codes shown to users that the product never issued, per day

**What is it?** The share of redemption attempts each day that present a code the product has no record of issuing. A model-invented code, if one ever reached a user, would land here the moment the user tries it.

**How we measure it.** Every call to `POST /verify` records one outcome: `redeemed`, `already_used`, `expired`, `unknown` or `rate_limited`. The number is `unknown` ÷ (all attempts except `rate_limited`), per UTC day. The outcome is stored; the value presented is not (CP-3). The outcome record does not exist yet (see upstream fixes).

**Target.** Lower is better; 0 is the ideal. There is no baseline for this product, so the numeric target is set after the first 14 days of data. The Irregular figure above (20 of 50 repeats) measures chatbots writing passwords directly, not this flow, so it is not used as a baseline.

**Why it shows the need was met.** The need is "no invented values". A code the model made up is by definition one the product cannot find. The number would not fall from using the feature more; it can only fall if invention stops.

**What it can't tell you.** Mistyped codes also count as `unknown`, so it has a floor above 0, and it cannot see an invented value that the user never tries to redeem. The supporting metric below covers that second case for the agent's replies.

### Supporting metrics

| Metric | What it is | Recorded from | Why it explains the success metric |
|---|---|---|---|
| **Replies with an unbacked value, per day** | Agent replies that contain a code, key or number that no tool returned in the same conversation | The agent's own check of each reply against that conversation's tool returns (the EV-2 comparison, run on every reply) | If the success metric rises and this does too, the model is inventing values; look at the agent's prompt and model. If this is 0, look at typos and replay attempts. |
| **Issued tokens redeemed before expiry, per day** | Share of tokens issued that day that were redeemed successfully once before they expired | Token table (issued, redeemed, expiry times) | If the success metric rises and this falls, real codes are not reaching users or expiring too soon; look at the agent's display and the expiry default (FR-2.1). |

## Guarantee metrics

| Metric | Requirement | Threshold | Measured on | Recorded from |
|---|---|---|---|---|
| **Tool-call time, p95** | PF-1 | under 50 ms | p95 over 1,000 consecutive calls on a warm pool, requests of 64 bytes or less | Duration on each tool call in the structured log |
| **Server-caused errors under load** | PF-2 | 0 errors with 100 callers in flight | Count of errors other than `invalid_argument` and `unauthorized`, over each 30 s window | Errors per code series (OB-4) |
| **Qrypt requests, worst 10 seconds** | DE-1 | at most 30 | Largest count in any sliding 10 s window, per day | Upstream request timestamps (OB-4 series) |
| **Entropy served per Qrypt request** | PF-3 | at least 64 KiB | KiB served ÷ upstream requests, per day, startup check excluded | Budget meter and upstream request count |
| **Qrypt use against the cap** | VR-7, GC-4 | never above 10,240 KiB; alert at 80% (8,192 KiB) | Cumulative KiB fetched, read daily | Budget meter |
| **Time to "unavailable" when Qrypt is down** | RL-1 | within 2 s of the pool being unable to cover a call | Worst case per outage | Timestamp pair: pool-cannot-cover event and error returned |

## Upstream fixes and open questions

- **Redemption outcomes** → add a functional row under FR-2 and an observability row: every `POST /verify` records its outcome (including `unknown` and `rate_limited`) with no value.
- **Reply check on every agent reply** → add to EV-2 (or an OB row): the agent runs the tool-return comparison at runtime and records a count, not only in tests.
- **Call duration** → OB-2 lists timestamp, level, event and client id but not duration; add duration, and a latency series to OB-4.
- **Time to "unavailable"** → add the two events (pool cannot cover, error returned) to the OB-2 log events so RL-1 can be read from logs.
- **Success target** → set the numeric target for the success metric after 14 days of data.
- **Success sentence** → the owner agrees or rewords it before the design relies on it.
