### S11 — A call the pool cannot cover fails whole, within 2 seconds, with the right reason

**Kind:** module
**Blocked by:** S10
**Review gate:** agents
**Size:** M — one wait, five failure paths and the log events (agent 10–20 min)
**Builds:** PoolStarved reasons mapping (Entropy source / Meter error to reason); A call the pool cannot cover removes 0 bytes (VR-8); Two events logged when the pool first cannot cover a call and when the error returns (OB-8); Bytes already in the pool are served after the budget is spent (Decision 2, A-13); Upstream 429 while the pool holds enough: normal answer, backoff events; Pool cannot cover within 2 s: temporarily_unavailable (RL-1, RL-3); Budget spent and pool cannot cover: budget_exhausted; Time to unavailable: the two OB-8 log events
**Extends:** Pool.take and the refill loop (S10)
**Stubs:** Meter.reserve and Entropy source.fetch (skeleton stubs), removed by S20
**Requirements:** CT-4, FR-5.5, OB-8, RL-1, RL-2, RL-3, VR-8
**Guards:** RL-2, RL-6

#### Why
When Qrypt is slow, down or the budget is spent, callers get a clear error quickly and never a partial or invented value.

#### How it's checked
The agent injects each upstream fault into stubs and times the error.

#### Acceptance criteria
AC1. A take waits for a refill that arrives in time
   Given a pool holding 100 bytes and a refill delayed by 1 s
   When take is called for 200 bytes
   Then it returns 200 bytes after about 1 s
   Evidence: gate case `pool-waits-for-refill`
AC2. A take gives up after 2 seconds
   Given a pool holding 100 bytes and a refill delayed by 3 s
   When take is called for 200 bytes
   Then it raises PoolStarved temporarily_unavailable at 2.0 to 2.2 s
   Evidence: gate case `pool-gives-up-2s`
AC3. A call that cannot be covered removes no bytes
   Given a pool holding 1000 bytes and the upstream down
   When take is called for 1001 bytes
   Then it fails and the pool still holds 1000 bytes
   Evidence: gate case `pool-fails-whole`
AC4. Each upstream error maps to its caller-facing reason
   Given the stub source raising RateLimited, Unavailable, Misconfigured and AllotmentUsed, and the stub meter raising BudgetExhausted
   When take cannot be covered for each
   Then the reasons are temporarily_unavailable, temporarily_unavailable, upstream_misconfigured, budget_exhausted and budget_exhausted
   Evidence: gate case `pool-reason-map`
AC5. Callers are served from the pool while 429s last
   Given the stub source returning 429 and a pool holding 10 KiB
   When 100 takes of 16 bytes are made
   Then all 100 succeed with normal answers
   Evidence: gate case `pool-serves-during-429`
AC6. The first failure and the returned error are both logged
   Given the stub source returning 429 and an empty pool
   When take is called
   Then an event for cannot-cover and an event for error-returned are logged with timestamps 2 s or less apart
   Evidence: gate case `pool-ob8-events`
AC7. Bytes already paid for are served after the budget is spent
   Given the stub meter exhausted and a pool holding 500 bytes
   When take is called for 100 bytes
   Then it returns 100 bytes
   Evidence: gate case `pool-serves-after-budget`
AC8. A call larger than what is left fails once the budget is spent
   Given the stub meter exhausted and a pool holding 400 bytes
   When take is called for 1000 bytes
   Then it raises PoolStarved budget_exhausted and the pool still holds 400 bytes
   Evidence: gate case `pool-exhausted-too-big`

#### Not in this story
- The startup check and status: picked up by S12
