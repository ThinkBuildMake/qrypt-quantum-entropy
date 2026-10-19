### S35 — Wire health and metrics: the hosted service shows what the running server reports

**Kind:** wiring
**Blocked by:** S20, S28, S29, S30
**Review gate:** human demo
**Size:** M — one flow across two processes through the database (agent 10–20 min)
**Wires:** D-11 getHealth, D-12 getMetrics and D-15 Status report. Removes the stub server_status writer and the stub Pool.status, Issuance.callStats and Entropy source.requestStats.
**Builds:** none (joins modules; builds nothing new)
**Extends:** none
**Stubs:** none
**Requirements:** AZ-1, DE-1, DE-5, FR-5.6, FR-5.7, FR-5.8, OB-1, OB-4, OB-9, PF-2
**Guards:** OB-1, OB-3, OB-9

#### Why
An operator sees the pool level, the upstream status and the operating series of a running server through the hosted service, and sees a bad Qrypt token named.

#### How it's checked
The human demo with curl against the HTTP service while the MCP server runs, stops and restarts.

#### Acceptance criteria
AC1. Health before any server has run is empty, not an error
   Given a database reset with `make db-reset` and no server ever started
   When GET /health is called
   Then the response is 200 with reporting false
   Evidence: demo: `curl -s $BASE/health`
AC2. A running server shows its pool and upstream
   Given the real MCP server running with the mock Qrypt
   When GET /health is called after 10 s
   Then reporting is true, reportAgeSeconds is 6 or less, the marks are 32768 and 131072, the level is above 0 and upstream.status is ok
   Evidence: demo; integration case `health-real-report`
AC3. A stopped server reads as not reporting within 15 seconds
   Given the MCP server is stopped
   When GET /health is called 16 s later
   Then reporting is false
   Evidence: demo; integration case `health-not-reporting`
AC4. A bad Qrypt token is named
   Given the MCP server started with the mock returning 401
   When GET /health is called and the server log is read
   Then upstream.status is misconfigured and the log event names the invalid or expired Qrypt token
   Evidence: demo; integration case `health-misconfigured`
AC5. Metrics show the series with real values
   Given 5 get_random_bytes calls and 1 invalid call just made
   When GET /metrics is called with the admin key
   Then calls per tool shows get_random_bytes 5 and errors per code shows invalid_argument 1, with the pool, upstream-rate, budget and duration series present
   Evidence: demo: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/metrics`; integration case `metrics-real-values`
AC6. Metrics need an admin key
   Given a client key and no key
   When GET /metrics is called
   Then the responses are 403 and 401
   Evidence: demo
AC7. A stopped server leaves only the report-age series
   Given the server stopped for 20 s
   When GET /metrics is called
   Then the pool and call series are absent and the report age is present
   Evidence: demo
AC8. The error and worst-10-seconds panels show real figures
   Given `make sample-metrics` scraping /metrics every 5 s during the calls above for at least 30 s
   When `make report SNAPSHOTS=snaps.json` runs
   Then the server-caused errors panel shows 0 and the worst-10-seconds panel shows the largest upstream request count seen and marks it within 30
   Evidence: demo: `make sample-metrics SECONDS=40` while calling, then `make report SNAPSHOTS=snaps.json`

#### Not in this story
- Load-test numbers: picked up by S36
