### S09 — Backoff after 429 and the 30-requests-per-10-seconds ceiling

**Kind:** module
**Blocked by:** S08
**Review gate:** agents
**Size:** M — one limiter and one retry policy (agent 10–20 min)
**Builds:** The 30-per-10-s rate limit holds however many callers (DE-1); Upstream 429: RateLimited after backoff, log event with attempt number
**Extends:** Entropy source.fetch (S08)
**Stubs:** none
**Requirements:** DE-1, DE-3
**Guards:** DE-1, GC-4

#### Why
Keeps the product under Qrypt's rate limit however many callers there are, and stops a 429 from turning into a retry storm.

#### How it's checked
The agent drives the client against the mock with a scaled clock and reads the mock's request timestamps.

#### Acceptance criteria
AC1. After a 429 the waits double and are capped
   Given the mock returns 429 continuously and a test clock
   When fetch retries
   Then the nominal waits are 0.5, 1, 2, 4, 8, 16, 32 then 60 s at most
   Evidence: gate case `backoff-nominal`
AC2. Each wait is jittered between 50% and 100% of nominal
   Given two runs with different jitter seeds
   When each waits after 429
   Then every wait is within 50% to 100% of nominal and the two runs' waits differ
   Evidence: gate case `backoff-jitter`
AC3. 60 seconds of 429 cost at most 8 requests
   Given the mock returns 429 for 120 s on the test clock
   When fetch retries for the first 60 s
   Then the mock logged 8 requests or fewer
   Evidence: gate case `backoff-60s`
AC4. Still limited after the backoff raises RateLimited with the attempt number logged
   Given the mock returns 429 until retries are exhausted
   When fetch is called
   Then it raises RateLimited and the log event holds the attempt number
   Evidence: gate case `rate-limited-raised`
AC5. 100 concurrent callers never exceed 30 requests in 10 seconds
   Given the mock enforcing the 30-per-10-s limit
   When 100 callers fetch at once, with some 429 retries
   Then the largest count of requests in any sliding 10 s window is 30 or fewer, retries included
   Evidence: gate case `rate-ceiling-100`
AC6. A retry after 429 is the same batch
   Given the mock returns 429 once, then 200
   When fetch is called for one reservation
   Then the mock logged 2 requests for 1 reservation
   Evidence: gate case `retry-same-batch` (the batch count is checked in S39)

#### Not in this story
- Choosing when to refill: picked up by S10
