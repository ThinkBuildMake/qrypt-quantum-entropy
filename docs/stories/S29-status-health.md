### S29 — GET /health says whether the server is reporting and what its pool and upstream look like

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — one route with five states (agent 10–20 min)
**Builds:** getHealth GET /health; /health answers in 200 ms or less once awake (OB-1); A report older than 15 s reads as not reporting (OB-1); With more than one row, health and metrics use the most recently reporting one; With no report row, /health shows an empty state, not an error (FR-5.6); Database unavailable: 503 database_unavailable
**Extends:** none
**Stubs:** The writer of server_status (skeleton stub), removed by S35
**Requirements:** DE-5, FR-5.6, FR-5.7, FR-5.8, OB-1
**Guards:** OB-1, FR-5.6

#### Why
An operator, including one whose Qrypt token has expired, sees pool level and upstream status at a glance, and an empty system shows an empty state rather than an error.

#### How it's checked
The agent seeds server_status rows of different ages and calls /health.

#### Acceptance criteria
AC1. A fresh report shows pool and upstream
   Given a row reported 3 s ago with level 98304, marks 32768 and 131072 and upstream ok
   When GET /health is called with no key
   Then the response is 200 with reporting true, reportAgeSeconds about 3, the pool numbers and upstream status ok with lastSuccessAt
   Evidence: `curl -s $BASE/health`
AC2. An old report reads as not reporting
   Given a row reported 16 s ago
   When GET /health is called
   Then reporting is false with pool and upstream null
   Evidence: `curl -s $BASE/health`
AC3. No row at all is an empty state
   Given an empty server_status table
   When GET /health is called
   Then the response is 200 with reporting false
   Evidence: `curl -s $BASE/health`
AC4. The most recent row is used
   Given two rows reported 40 s and 2 s ago
   When GET /health is called
   Then the pool numbers are the 2 s row's
   Evidence: `curl -s $BASE/health`
AC5. A misconfigured upstream is shown
   Given a row with upstream status misconfigured
   When GET /health is called
   Then upstream.status is misconfigured
   Evidence: `curl -s $BASE/health`
AC6. A database outage is a 503
   Given the database is stopped
   When GET /health is called
   Then the response is 503 with error database_unavailable
   Evidence: gate case `health-db-down`
AC7. Health answers within 200 ms
   Given an awake service and a local database
   When GET /health is called 100 times
   Then p95 is 200 ms or less
   Evidence: gate case `health-latency`

#### Not in this story
- Writing the rows: picked up by S28
