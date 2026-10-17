### S41 — The test-results file and the 80% coverage gate

**Kind:** module
**Blocked by:** S36, S37, S38, S39, S40
**Review gate:** agents
**Size:** M — one generator over four existing result sets (agent 10–20 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** EV-4, EV-5, EV-6, GC-2, GC-3, PF-1
**Guards:** EV-4

#### Why
Gives the reviewer one file with coverage, speed, load and evaluation numbers, and makes the 80% coverage line a failing check.

#### How it's checked
The agent runs the generator and the completeness check.

#### Acceptance criteria
AC1. Coverage of core logic is at least 80% and CI fails below it
   Given the full suite
   When `make coverage` runs, and again with the threshold lifted to 99%
   Then it reports 80% or more for pool accounting, sampling, budget, token issue and verify and failure handling, and exits non-zero at 99%
   Evidence: `make coverage; echo $?`
AC2. The results file holds every required number
   Given the load, evaluation and coverage outputs
   When `make results` runs
   Then the file holds coverage, throughput, error rate, p50, p95 and p99, KiB per upstream request, and the golden and adversarial tables
   Evidence: `make results && make results-check`
AC3. It names the database and model used
   Given the generated file
   When it is read
   Then it names the Docker Compose Postgres, the model name, its limit and the request count
   Evidence: `make results-check`
AC4. A missing section fails the check
   Given a copy of the file with the adversarial table removed
   When `make results-check` runs on it
   Then it exits non-zero naming the missing section
   Evidence: `make results-check FILE=broken.md; echo $?`
AC5. The suite runs offline and gives identical results three times
   Given outbound network blocked
   When the unit suite runs 3 times in a row
   Then all three runs give the same results with 0 network calls and no Qrypt or model key
   Evidence: `make offline-triple`

#### Not in this story
- Publishing the file with the submission: picked up by S46
