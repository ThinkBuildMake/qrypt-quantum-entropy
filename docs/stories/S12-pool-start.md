### S12 — The pool starts with a 1 KiB check that names a bad Qrypt token

**Kind:** module
**Blocked by:** S10
**Review gate:** agents
**Size:** S — one start sequence and the status values (agent 5–10 min)
**Builds:** Pool.status(); Pool.start(); startupCheck (once at process start); Upstream 401: upstream_misconfigured
**Extends:** Pool refill loop (S10)
**Stubs:** Meter.reserve and Entropy source.fetch (skeleton stubs), removed by S20
**Requirements:** DE-5, FR-5.8, OB-1
**Guards:** GC-4

#### Why
An operator whose Qrypt token is invalid or expired learns it at startup, named as a configuration problem, instead of from failing users.

#### How it's checked
The agent starts the pool against the mock with a good and a bad token and reads its status and log.

#### Acceptance criteria
AC1. A good token passes the startup check and the refill loop begins
   Given the mock accepting the token
   When start is called
   Then one 1 KiB request is made with purpose startup_check, status is ok with lastSuccessAt set and the level is above 0 after the first refill
   Evidence: gate case `pool-start-ok`
AC2. A bad token is named and the process keeps running
   Given the mock returning 401
   When start is called
   Then status is misconfigured, the log event names the invalid or expired Qrypt token and the process is still running
   Evidence: gate case `pool-start-401`
AC3. A caller sees upstream_misconfigured, never a generic error
   Given status is misconfigured
   When take is called
   Then it raises PoolStarved upstream_misconfigured
   Evidence: gate case `pool-misconfigured-take`
AC4. Status is degraded after a failed fetch while the pool still covers calls
   Given a started pool and one failed fetch [assumed meaning]
   When status is read
   Then it is degraded
   Evidence: gate case `pool-status-degraded`
AC5. Status is failing when the pool cannot cover a call
   Given a started pool that cannot cover a call [assumed meaning]
   When status is read
   Then it is failing
   Evidence: gate case `pool-status-failing`
AC6. Status returns to ok after a successful fetch
   Given a failing pool and a successful fetch
   When status is read
   Then it is ok with lastSuccessAt set
   Evidence: gate case `pool-status-recovers`

#### Not in this story
- Writing status to the database and serving /health: picked up by S28 and S29
