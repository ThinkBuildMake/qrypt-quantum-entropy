### S28 — The MCP server reports its pool and upstream state to the database every 5 seconds

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — one timer job and one table write path (agent 10–20 min)
**Builds:** statusReport (every 5 s inside the MCP server); server_status (write path); Only Status writes server_status; a report holds no entropy and no value (OB-9); snapshot counters are cumulative since process start; Report write fails: skipped, retried next tick, tool calls unaffected; Server-caused errors: MetricsSnapshot.errorsByCode; Qrypt requests worst 10 s: MetricsSnapshot.upstreamRequestsLast10s
**Extends:** none
**Stubs:** Pool.status, Issuance.callStats and Entropy source.requestStats (skeleton stubs), removed by S35
**Requirements:** CP-2, OB-4, OB-9
**Guards:** OB-3

#### Why
The hosted service cannot see the server's memory, so this is how an operator can see the pool and how the metrics series cross the process boundary.

#### How it's checked
The agent runs the report job against stubbed pool, call and request statistics and reads server_status.

#### Acceptance criteria
AC1. A row is written every 5 seconds
   Given a running job with a stub pool at level 98304, marks 32768 and 131072 and upstream ok
   When 11 seconds pass
   Then one server_status row exists for the instance and reported_at advanced at least twice
   Evidence: gate case `status-every-5s`; `psql -c 'SELECT reported_at FROM server_status'`
AC2. The row holds no entropy and no value
   Given a stub pool holding a marker byte sequence
   When the row and snapshot are read and searched
   Then there are 0 matches for the marker
   Evidence: gate case `status-no-entropy`
AC3. The snapshot has every MetricsSnapshot field
   Given stub call statistics and request statistics
   When the snapshot is read
   Then it holds startedAt, upstreamRequestsLast10s, callsByTool, errorsByCode and toolCallDurationMs with p50, p95 and p99
   Evidence: gate case `status-snapshot-shape`
AC4. Counters are cumulative since process start
   Given 3 calls then 2 more calls
   When two reports are read
   Then callsByTool shows 3 and then 5
   Evidence: gate case `status-cumulative`
AC5. A failed write is skipped and retried
   Given the database refusing one write
   When the next tick arrives
   Then an error event is logged, tool calls are unaffected and the next report is written
   Evidence: gate case `status-write-fails`
AC6. A restart adds a new row and leaves the old one
   Given one row exists
   When the process restarts and reports
   Then 2 rows exist with different instance_id values
   Evidence: gate case `status-restart-new-row` [assumed A-15]

#### Not in this story
- Serving the row over HTTP: picked up by S29 and S30
