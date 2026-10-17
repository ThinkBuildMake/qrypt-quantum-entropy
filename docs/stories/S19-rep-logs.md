### S19 — The metrics report computes five key metrics from logs and status snapshots

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — five panels over two kinds of input (agent 10–20 min)
**Builds:** Panel: tool-call time p95; Panel: server-caused errors per 30 s window; Panel: Qrypt requests, worst 10 seconds; Panel: replies with an unbacked value per day; Panel: time to unavailable
**Extends:** none
**Stubs:** The writers of the logs and snapshots it reads (skeleton stubs), removed by S20, S32 and S35
**Requirements:** DE-1, OB-7, OB-8, PF-1, PF-2, RL-1
**Guards:** PF-1, PF-2, DE-1, RL-1

#### Why
Shows whether the product is fast, steady, inside Qrypt's limit and honest, from the logs and snapshots the system already writes.

#### How it's checked
The agent feeds synthetic log files and snapshots to the report.

#### Acceptance criteria
AC1. Tool-call p95 over 1,000 calls
   Given a log with 1,000 tool-call lines of durations 1 to 1000 ms
   When `make report LOG=calls.log` runs
   Then the p95 panel shows 950 ms and marks it above the 50 ms threshold
   Evidence: `make report LOG=calls.log | grep -A2 p95`
AC2. Server-caused errors exclude invalid_argument and unauthorized
   Given two snapshots 30 s apart with errorsByCode invalid_argument 5 to 9, temporarily_unavailable 0 to 3 and unauthorized 1 to 2
   When `make report SNAPSHOTS=snaps.json` runs
   Then the errors panel shows 3 for the window
   Evidence: `make report SNAPSHOTS=snaps.json | grep -A2 'server-caused'`
AC3. Worst 10 seconds is the largest sample
   Given samples of upstreamRequestsLast10s of 12, 30 and 7
   When `make report SNAPSHOTS=snaps.json` runs
   Then the panel shows 30 and marks it within the limit of 30
   Evidence: `make report SNAPSHOTS=snaps.json | grep -A2 'worst 10'`
AC4. Replies with an unbacked value are counted per day
   Given 10 reply-check events on one day of which 2 have backed fewer than found
   When `make report LOG=agent.log` runs
   Then the panel shows 2 for that day
   Evidence: `make report LOG=agent.log | grep -A2 unbacked`
AC5. Time to unavailable is the gap between the two events
   Given a cannot-cover event at 12:00:00.0 and an error-returned event at 12:00:01.4
   When `make report LOG=calls.log` runs
   Then the panel shows 1.4 s and marks it within the 2 s limit
   Evidence: `make report LOG=calls.log | grep -A2 unavailable`
AC6. A sampler saves /metrics scrapes as the snapshot history
   Given a running HTTP service whose /metrics counters change
   When `make sample-metrics` runs for 12 s with a 5 s interval
   Then the snapshots file gains at least 2 JSON lines, each with a timestamp and the counters, and `make report SNAPSHOTS=` reads it
   Evidence: `make sample-metrics SECONDS=12 && wc -l snaps.json`
AC7. Every panel reads no data without input
   Given no log or snapshot file
   When `make report` runs
   Then the five panels read no data and the command exits 0
   Evidence: `make report; echo $?`

#### Not in this story
- The four panels that read tables: picked up by S18
