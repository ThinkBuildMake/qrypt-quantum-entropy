### S07 — The entropy budget is counted in the database and never passes the cap

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — one atomic interface, two table write paths and the alerts (agent 10–20 min)
**Builds:** Meter.reserve(wantedKib, purpose, source); Meter.markBatch(batchId, outcome); budget (write path); batches (write path); Only Meter writes budget and batches; used_kib equals the sum of batches.kib; failed requests still count (RL-4); Total granted never exceeds the cap, under races and across processes (VR-7); A grant is sized to what remains when smaller than a refill; Budget alert: one log event at 80% and one at 100% (OB-5, D-16); Nothing remains: BudgetExhausted thrown, 100% event once (the tool error budget_exhausted is shown by the PoolStarved mapping); Entropy per Qrypt request: batches.purpose (startup check excluded); Qrypt use against the cap: budget.used_kib
**Extends:** none
**Stubs:** Entropy source and Pool (the callers), removed by S20
**Requirements:** DE-5, FR-5.4, FR-5.5, OB-5, PF-3, RL-4, VR-7
**Guards:** RL-4, VR-7, GC-4

#### Why
Makes the 10,240 KiB allotment a hard limit that holds across restarts, parallel refills and several server processes, and records every upstream request as a batch.

#### How it's checked
The agent runs the reserve interface with many callers and two processes against Compose Postgres and reads the batches table.

#### Acceptance criteria
AC1. A reservation is granted and recorded
   Given cap 10240 and used 0
   When reserve is called for 64 KiB with purpose refill and source qrypt
   Then it returns a batch of 64 KiB, used_kib is 64 and one batches row exists with outcome requested
   Evidence: gate case `reserve-grants`; `psql -c 'SELECT used_kib FROM budget'`
AC2. A grant is sized to what remains
   Given cap 100 and used 90
   When reserve is called for 64 KiB
   Then it grants 10 KiB and used_kib is 100
   Evidence: gate case `reserve-sized-to-remaining`
AC3. An exhausted budget refuses and adds nothing
   Given cap 100 and used 100
   When reserve is called for 64 KiB
   Then it throws BudgetExhausted, no batches row is added and one 100% event is logged
   Evidence: gate case `reserve-exhausted`
AC4. 100 racing reservations never pass the cap
   Given cap 1000 KiB and 100 callers
   When each calls reserve for 64 KiB at once
   Then the granted KiB sum to exactly 1000 and used_kib equals the sum of batches.kib
   Evidence: gate case `reserve-race-100`
AC5. Two server processes share one cap
   Given cap 1000 KiB and two processes on one database
   When both call reserve for 64 KiB in a loop until refused
   Then the granted KiB across both sum to exactly 1000
   Evidence: gate case `reserve-two-processes`
AC6. A failed batch still counts and markBatch changes no total
   Given a reserved batch of 64 KiB
   When markBatch is called with failed
   Then the batch outcome is failed with settled_at set and used_kib is unchanged
   Evidence: gate case `mark-batch-failed-counts`
AC7. The 80% and 100% alerts fire once each
   Given cap 100 KiB
   When reservations bring use to 80 and then to 100 KiB, then another is tried
   Then exactly one 80% event and one 100% event are logged
   Evidence: gate case `budget-alerts`
AC8. A cap lowered below use leaves the budget exhausted
   Given used_kib 128 and the cap lowered to 100 by the migrator
   When reserve is called for 64 KiB
   Then it throws BudgetExhausted and used_kib stays 128
   Evidence: gate case `reserve-after-cap-lowered` [assumed A-4]
AC9. The startup check batch carries its purpose
   Given cap 10240 and used 0
   When reserve is called for 1 KiB with purpose startup_check
   Then the batches row has purpose startup_check and kib 1 so the report can leave it out
   Evidence: gate case `reserve-startup-purpose`
AC10. The meter survives a restart
   Given used_kib 64
   When the database and the process are restarted
   Then used_kib is still at least 64
   Evidence: gate case `meter-survives-restart`

#### Not in this story
- Reading and changing the cap over HTTP: picked up by S24
- The pool calling reserve: picked up by S10 and wired in S20
