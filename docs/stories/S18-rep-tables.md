### S18 — The metrics report computes four key metrics from the database

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — four panels over four tables (agent 10–20 min)
**Builds:** Panel: success metric per UTC day; Panel: tokens redeemed before expiry; Panel: KiB served per Qrypt request; Panel: Qrypt use against the cap with 80% alert level
**Extends:** none
**Stubs:** The writers of the rows it reads (skeleton stubs), removed by S20 and S33
**Requirements:** CO-1, FR-5.6, GC-4, PF-3
**Guards:** GC-4

#### Why
Turns the success metric and three supporting numbers into daily figures anyone can read, so the owner can see whether codes are being invented.

#### How it's checked
The agent seeds synthetic rows and runs the report.

#### Acceptance criteria
AC1. The success metric is unknown over attempts except rate_limited
   Given outcomes on 2026-10-17 of 3 redeemed, 1 already_used, 1 expired, 2 unknown and 4 rate_limited
   When `make report` runs
   Then the success panel shows 2 of 7 for 2026-10-17
   Evidence: `make report | grep -A2 'never issued'`
AC2. A day with only rate_limited attempts shows no attempts, not an error
   Given outcomes of 4 rate_limited on one day
   When `make report` runs
   Then the success panel shows no attempts for that day and the command exits 0
   Evidence: `make report; echo $?`
AC3. Redeemed-before-expiry excludes seed tokens
   Given 4 issued tokens of which 2 were redeemed before expiry, and 1 seed token redeemed
   When `make report` runs
   Then the panel shows 2 of 4
   Evidence: `make report | grep -A2 'before expiry'`
AC4. KiB per upstream request excludes the startup check
   Given batches of one startup_check of 1 KiB and two refills of 64 KiB, and issuances consuming 131072 bytes
   When `make report` runs
   Then the panel shows 64 KiB per request
   Evidence: `make report | grep -A2 'per Qrypt request'`
AC5. Use against the cap shows the alert level
   Given used_kib 8300 of 10240
   When `make report` runs
   Then the panel shows 8,300 KiB, 81%, above the 8,192 KiB alert level
   Evidence: `make report | grep -A2 'against the cap'`
AC6. Every panel reads no data on an empty database
   Given an empty database
   When `make report` runs
   Then the four panels read no data and the command exits 0
   Evidence: `make report; echo $?`

#### Not in this story
- The five panels that read logs and status snapshots: picked up by S19
