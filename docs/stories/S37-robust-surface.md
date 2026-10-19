### S37 — Every route and tool obeys its role, validation and speed limits

**Kind:** module
**Blocked by:** S20, S24, S23, S21, S29, S30, S05
**Review gate:** agents
**Size:** M — one pass over every surface; it needs every route real, so it cannot run on stubs (agent 10–20 min)
**Builds:** 0 calls succeed outside the role a route or tool allows (AZ-1)
**Extends:** the matrix and fuzz harnesses built in S01
**Stubs:** none
**Requirements:** AZ-1, AZ-2, CT-2, CT-4, GC-7, PF-5, RL-5
**Guards:** CT-2, RL-5, GC-7

#### Why
Closes the gaps a reviewer would try first: the wrong key on the wrong route, malformed input, a hosted route that returns entropy, and slow answers.

#### How it's checked
The agent runs the committed matrix, fuzz and timing cases against the real service.

#### Acceptance criteria
AC1. No call succeeds outside its role
   Given every registered tool and route
   When each is called with no key, a client key and an admin key
   Then 0 calls succeed outside the role the architecture allows and every refusal is 401 or 403
   Evidence: `make gate` prints the matrix cell count
AC2. 1,000 malformed inputs cause no unhandled error
   Given every tool and HTTP route
   When 1,000 malformed inputs are sent
   Then 0 unhandled exceptions and 0 5xx occur and each returns a structured 4xx or tool error
   Evidence: `make fuzz`
AC3. A rejected call draws nothing
   Given every invalid case from the tool and route criteria
   When each is sent with the pool and meter recorded before and after
   Then the pool level and used_kib are unchanged for each
   Evidence: `make gate` case `rejected-draws-nothing`
AC4. No hosted route returns an entropy-derived value
   Given the deployed route list: POST /verify, the admin routes, GET /health and GET /metrics
   When each is called once with the right key
   Then none returns random bytes, an integer or an issued token
   Evidence: `make check-routes`
AC5. Each hosted route answers within 1 second at p95
   Given an awake service and a local database
   When 100 requests are sent to each of POST /verify, the admin routes and GET /metrics
   Then p95 is 1 s or less for each and the figures go to the results file
   Evidence: `make route-latency`
AC6. Every error code is exercised once
   Given the list of codes
   When one request per code is sent across the service
   Then invalid_argument, unauthorized, budget_exhausted, temporarily_unavailable and upstream_misconfigured each appear with their stable code
   Evidence: `make gate` case `error-codes-live`

#### Not in this story
- Value, memory and fault leaks: picked up by S38
