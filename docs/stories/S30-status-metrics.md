### S30 — GET /metrics exposes the operating series in Prometheus text for an admin

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** S — one route over two tables (agent 5–10 min)
**Builds:** getMetrics GET /metrics
**Extends:** none
**Stubs:** Access.authenticate in the HTTP service (skeleton stub), removed by S20; the writer of server_status (skeleton stub), removed by S35
**Requirements:** AZ-1, OB-4
**Guards:** OB-3

#### Why
Gives the operator the pool level, upstream request rate, budget use, calls, errors and latency percentiles in the format monitoring tools read.

#### How it's checked
The agent seeds a status row and a budget row and parses the response.

#### Acceptance criteria
AC1. The series are present
   Given a fresh status row and a budget row
   When GET /metrics is called with an admin key
   Then the response is 200 and holds pool level, upstream requests per 10 s, budget used, calls per tool, errors per code, tool-call duration p50, p95 and p99, and a report-age series
   Evidence: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/metrics`
AC2. The text parses as Prometheus exposition
   Given the response above
   When a Prometheus text parser reads it
   Then it parses without error
   Evidence: gate case `metrics-parse`
AC3. Live series are left out when the server is not reporting
   Given a row reported 16 s ago
   When GET /metrics is called
   Then the pool and call series are absent and the report-age series is present
   Evidence: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/metrics`
AC4. Values match the snapshot
   Given a snapshot with callsByTool get_random_bytes 7
   When GET /metrics is called
   Then the calls series for get_random_bytes is 7
   Evidence: gate case `metrics-values`
AC5. A client key or no key is refused
   Given a client key and no key
   When GET /metrics is called
   Then the responses are 403 forbidden and 401 unauthorized
   Evidence: two curls

#### Not in this story
- Writing the rows: picked up by S28
