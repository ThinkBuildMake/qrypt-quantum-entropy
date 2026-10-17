### S38 — No value, secret or pooled byte leaks anywhere

**Kind:** module
**Blocked by:** S20, S33, S36, S37
**Review gate:** agents
**Size:** M — one search pass over the integrated system (agent 10–20 min)
**Builds:** 
**Extends:** the integrated issue and redeem flows (S20, S33) and the runs of S36 and S37
**Stubs:** none
**Requirements:** CP-1, CP-2, CP-3, OB-2, OB-3, OB-6
**Guards:** CP-1, CP-2, CP-3, OB-3

#### Why
Shows the security review that nothing issued appears anywhere it should not, in a log, an error, a table, a file or the repository.

#### How it's checked
The agent runs a full suite with known markers, then searches files, logs, responses and tables.

#### Acceptance criteria
AC1. No issued value appears in logs, errors or responses
   Given the issue, redeem, fuzz and load runs with every returned value recorded
   When logs, error bodies and HTTP bodies are searched for each value in raw, hex and base64
   Then there are 0 matches
   Evidence: `make leak-scan`
AC2. No value is in the audit tables
   Given the same values
   When issuances, issuance_batches, redemption_outcomes and server_status are searched
   Then there are 0 matches
   Evidence: `make leak-scan`
AC3. No pooled byte is on disk
   Given a marker sequence served by the mock and a restart
   When every file the application wrote is searched
   Then there are 0 matches and the restarted pool is empty
   Evidence: `make leak-scan`
AC4. Every log line in the whole suite is JSON
   Given the logs captured across the full test suite
   When each line is parsed
   Then 100% parse and every tool-call line has duration_ms
   Evidence: `make leak-scan` prints lines parsed
AC5. The repository holds no secret
   Given full git history
   When the secret scan runs
   Then it finds 0 secrets and .env.example holds placeholders only
   Evidence: `make gate` case `secret-scan`

#### Not in this story
- Fault injection and the two-process budget: picked up by S39
- Production-only checks: picked up by S46
