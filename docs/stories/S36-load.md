### S36 — Latency and 100-caller load numbers, measured on the real system against the mock

**Kind:** module
**Blocked by:** S20
**Review gate:** agents
**Size:** L — a capability the feasibility check could not confirm (p95 under 50 ms and 100 callers with 0 errors while every call commits an audit row, HLD §2 flags the write rate); a new scripted load client; it measures the integrated system, so it cannot run against stubs (agent 20–45 min)
**Builds:** 
**Extends:** the integrated issue flow (S20)
**Stubs:** none
**Requirements:** DE-1, PF-1, PF-2, PF-3, SC-1, VR-1, VR-9
**Guards:** GC-4, CP-2

#### Why
Proves the product is fast and steady enough to be trusted: p95 under 50 ms, 100 callers with no errors, at least 64 KiB per Qrypt request, and the numbers are written down for the reviewer.

#### How it's checked
The agent runs the committed scripted client over stdio against the real server, the mock Qrypt (200 ms latency, 30-per-10-s limit) and Compose Postgres, and reads the results file.

#### Acceptance criteria
AC1. Latency p95 is under 50 ms on a warm pool
   Given a warm pool, the mock Qrypt and Compose Postgres
   When 1,000 calls mixed evenly across the three tools, each asking for 64 bytes or less, are made over stdio
   Then p95 is under 50 ms and p50, p95, p99 and the database used are written to the results file
   Evidence: `make load-latency`
AC2. 100 callers for 30 seconds give no server error
   Given the mock enforcing Qrypt's limit and adding 200 ms
   When 100 calls are kept in flight for 30 s
   Then 0 errors other than invalid_argument and unauthorized occur and throughput and error rate are written to the results file
   Evidence: `make load-concurrent`
AC3. At least 64 KiB are served per Qrypt request
   Given the run above
   When KiB served is divided by the number of upstream requests, the startup check excluded
   Then the ratio is 64 or more
   Evidence: `make load-concurrent` prints the ratio from the meter
AC4. The upstream ceiling holds under load
   Given the mock's request log from the run
   When the largest count in any sliding 10 s window is taken
   Then it is 30 or fewer
   Evidence: `make load-concurrent` prints the maximum
AC5. The pool stays between its marks
   Given the level sampled every 10 ms during the run
   When the samples are read
   Then the maximum is 128 KiB or less and the first refill began when the level fell below 32 KiB
   Evidence: `make load-concurrent` prints both
AC6. No byte was served twice across 10,000 calls
   Given 100 callers and at least 10,000 calls
   When every (batch, offset range) in issuance_batches is compared
   Then no two ranges overlap
   Evidence: `make load-concurrent` prints the overlap count
AC7. Every successful call has exactly one audit row
   Given the run above
   When successful calls are counted and issuances rows are counted
   Then the two numbers are equal and batches contain both batches of a call that crossed a boundary
   Evidence: `make load-concurrent` prints both counts

#### Not in this story
- Security and fault suites: picked up by S37, S38 and S39
- Gathering the numbers into the final results file: picked up by S41
