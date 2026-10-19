### S10 — The in-memory entropy pool hands each byte out once and refills itself

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** L — first async job (the background refill) and the concurrency proof (agent 20–45 min)
**Builds:** Pool.take(n); Pool.stop(); refill (background loop in the MCP server process); A byte offset in any batch is served to at most one caller (VR-1); Level never exceeds the high mark; refill starts below the low mark; configurable (SC-1); A refill asks for at least 64 KiB while the budget allows (PF-3); No pooled byte is written to disk, a log or an error message (CP-2, OB-3)
**Extends:** none
**Stubs:** Meter.reserve and Entropy source.fetch (skeleton stubs), removed by S20
**Requirements:** CP-2, OB-3, PF-3, SC-1, VR-1, VR-9
**Guards:** DE-1, GC-4, CP-2

#### Why
Lets 100 callers be served from a held supply, with no byte ever given to two of them, and keeps the upstream calls to large, infrequent batches.

#### How it's checked
The agent runs 100 concurrent takers against stubbed Meter and Entropy source and compares the byte ranges handed out.

#### Acceptance criteria
AC1. 100 concurrent callers are never given the same byte twice
   Given stubbed sources and 100 callers
   When they make 10,000 take calls of 16 bytes between them
   Then no (batch, offset range) appears in two results
   Evidence: gate case `pool-no-overlap`
AC2. A take across a batch boundary returns both slices
   Given a pool holding 3 bytes of batch A then 5 bytes of batch B
   When take is called for 6 bytes
   Then the result has slices A offset 0 count 3 and B offset 0 count 3 and exactly those bytes
   Evidence: gate case `pool-slices`
AC3. A refill starts when the level falls below the low mark
   Given low mark 32 KiB, high mark 128 KiB and a pool at 40 KiB
   When 10 KiB is taken
   Then exactly one refill starts and the stub source is asked for 64 KiB or more
   Evidence: gate case `pool-refill-below-low`
AC4. The level never passes the high mark under load
   Given the 100-caller run
   When the level is sampled every 10 ms
   Then the maximum sampled level is 128 KiB or less
   Evidence: gate case `pool-high-mark`
AC5. Only one refill is ever in flight
   Given 100 callers draining the pool at once
   When refills are triggered
   Then the stub source never has 2 fetches open at the same time
   Evidence: gate case `pool-single-refill`
AC6. The marks are configurable
   Given low mark 8 KiB and high mark 16 KiB in the environment
   When the pool fills and drains
   Then the refill starts below 8 KiB and the level stays at 16 KiB or less
   Evidence: gate case `pool-marks-config`
AC7. Pooled bytes never reach a file, a log or an error
   Given the stub source returns a known marker byte sequence
   When a run completes, an error is raised and the pool is restarted
   Then 0 matches for the marker in files, logs and error messages and the restarted pool is empty
   Evidence: gate case `pool-memory-only`
AC8. Stopping the pool discards what it holds
   Given a pool holding 64 KiB
   When stop is called
   Then the level is 0
   Evidence: gate case `pool-stop`

#### Not in this story
- Waiting for a refill, the 2 s limit and failure reasons: picked up by S11
- The startup check, start and status: picked up by S12
