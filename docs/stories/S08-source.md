### S08 — The Qrypt client fetches entropy and fails plainly, tested against a committed mock

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** L — first contact with an external service, with the mock service built alongside (agent 20–45 min)
**Builds:** Entropy source.fetch(sizeKib); Entropy source.requestStats(); GET /api/v1/quantum-entropy?size={n} on Qrypt; Committed mock Qrypt service (limit, latency, fault injection, request log); Stub source labelled test_source; Only 1 to 512 KiB is ever requested (DE-2); A failure is raised, never replaced by another source (RL-2, FR-6.1); The access token is never logged; Upstream 401: Misconfigured, operator-facing log event naming the cause; Upstream 403: AllotmentUsed, log event; Upstream 5xx, timeout, DNS: Unavailable, log event
**Extends:** none
**Stubs:** none
**Requirements:** CH-1, DE-2, DE-4, DE-5, FR-6.1, OB-4, RL-2
**Guards:** CP-1, GC-4

#### Why
Brings in the only entropy source with every upstream fault named, and gives every test a committed stand-in so the suite never touches the live service.

#### How it's checked
The agent runs the client against the mock service with each fault injected and reads the mock's request log.

#### Acceptance criteria
AC1. A fetch returns exactly the bytes asked for
   Given the mock Qrypt service answering in Qrypt's shape
   When fetch is called for 64 KiB
   Then it returns 65536 bytes and the mock logged one GET with size=64 and a bearer token
   Evidence: gate case `fetch-shape`
AC2. Sizes outside 1 to 512 KiB are never sent
   Given the mock service
   When fetch is called with 0, 513, 1 and 512
   Then 0 and 513 raise an error with 0 requests sent and 1 and 512 are sent
   Evidence: gate case `fetch-bounds` compares the mock's request log
AC3. Upstream 401 is reported as a configuration error
   Given the mock returns 401
   When fetch is called
   Then it raises Misconfigured and the log event names the invalid or expired token
   Evidence: gate case `fetch-401`
AC4. Upstream 403 is reported as the allotment used
   Given the mock returns 403
   When fetch is called
   Then it raises AllotmentUsed and a log event is written
   Evidence: gate case `fetch-403`
AC5. Upstream 5xx and connection failure are reported as unavailable
   Given the mock returns 500, then 503, then drops the connection
   When fetch is called for each
   Then each raises Unavailable and logs an event
   Evidence: gate case `fetch-5xx`
AC6. A reply slower than 5 s is abandoned
   Given the mock delays its reply by 6 s
   When fetch is called
   Then it raises Unavailable at about 5 s and the request is abandoned
   Evidence: gate case `fetch-timeout`
AC7. There is no other source and the token is never logged
   Given the configuration and all the logs of the cases above
   When the settings are listed and the logs searched for the Qrypt token and the returned bytes
   Then no setting selects another source and there are 0 matches
   Evidence: `make config-show | grep -i source` shows no switch; gate case `fetch-no-leak`
AC8. A second source can be swapped in
   Given the stub source labelled test_source
   When fetch is called through the same interface
   Then it returns bytes with the label test_source
   Evidence: gate case `stub-source`
AC9. Request statistics count each HTTP request
   Given five fetches against the mock
   When requestStats is called
   Then requestsLast10s is 5
   Evidence: gate case `request-stats`

#### Not in this story
- Retry, backoff and the 30-per-10-s limit: picked up by S09
- Reserving budget and holding the bytes: picked up by S07 and S10
