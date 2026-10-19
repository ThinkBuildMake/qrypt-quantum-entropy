### S04 — The services survive a database restart and fail plainly while it is down

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** S — one shared connection layer and its error mapping (agent 5–10 min)
**Builds:** Database unavailable during authenticate: temporarily_unavailable / 503; Database unavailable: temporarily_unavailable; Database unavailable: 503 temporarily_unavailable; Database connections recover from a restart; calls during an outage fail with temporarily_unavailable
**Extends:** none
**Stubs:** none
**Requirements:** RL-6, RL-7
**Guards:** RL-6

#### Why
The free host may restart the database at any time; the services must reconnect without a restart and must not return a value while a record cannot be written.

#### How it's checked
The agent restarts the Compose database under a running service and checks the responses.

#### Acceptance criteria
AC1. The connection layer reconnects after a restart
   Given a running service with an open connection pool
   When the Compose database is restarted and a read is made within 30 s
   Then the read succeeds with no process restart
   Evidence: gate case `db-restart-reconnect` (`docker compose restart db`)
AC2. An HTTP call during an outage returns 503
   Given the database is stopped
   When a database-backed HTTP route other than health is called
   Then the response is 503 with error temporarily_unavailable (the health route's own database_unavailable is checked in S29)
   Evidence: gate case `http-db-down`
AC3. A tool call during an outage returns temporarily_unavailable with no value
   Given the database is stopped
   When a tool is called
   Then the tool error code is temporarily_unavailable and the result holds no value
   Evidence: gate case `tool-db-down`
AC4. Acknowledged writes survive a database restart
   Given 100 rows written through the connection layer and acknowledged
   When the Compose database is restarted
   Then all 100 rows are still there
   Evidence: gate case `acknowledged-writes-survive`

#### Not in this story
- Each route's own empty or error criteria: picked up by the story that owns the route
