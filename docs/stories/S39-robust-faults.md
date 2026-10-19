### S39 — Every upstream fault fails closed, and the budget holds across processes and retries

**Kind:** module
**Blocked by:** S20
**Review gate:** agents
**Size:** M — four integration passes over the real service (agent 10–20 min)
**Builds:** 
**Extends:** the integrated issue flow (S20)
**Stubs:** none
**Requirements:** CH-1, DE-3, FR-6.1, RL-2, VR-7
**Guards:** RL-2, VR-7, GC-4

#### Why
Shows the review that each Qrypt fault is refused with the right code rather than papered over, that two servers cannot overspend, and that a retry or a second source behaves as designed.

#### How it's checked
The agent injects each fault into the real service through the mock and reads the responses, the batches table and the mock's request log.

#### Acceptance criteria
AC1. Each upstream fault fails closed with the right code
   Given pool drained and the mock returning 403, 500, 503, a 6 s delay and a dropped connection one at a time
   When get_random_bytes is called for each
   Then 403 gives budget_exhausted and the others give temporarily_unavailable in 100% of calls, and 0 values or audit rows carry a source other than qrypt in the whole suite
   Evidence: `make fault-suite`
AC2. Two server processes share one cap
   Given cap 1000 KiB and two MCP server processes on one database against the mock
   When both are driven with 100 callers until refused
   Then the KiB granted across both are 1000 or fewer and the meter equals the non-429 requests in the mock's log [assumed P3]
   Evidence: `make two-process-budget`
AC3. A 429 retry stays one batch
   Given the real service and the mock returning 429 once and then 200
   When the pool refills
   Then batches shows 1 row and used_kib counts 64 KiB once [assumed P3]
   Evidence: `make two-process-budget` includes `retry-one-batch`
AC4. A second source flows through to metadata and audit
   Given the stub source test_source plugged into the real service
   When get_random_bytes is called
   Then the metadata source and the batches.source are both test_source
   Evidence: gate integration case `second-source-label`

#### Not in this story
- Leaks of values and secrets: picked up by S38
