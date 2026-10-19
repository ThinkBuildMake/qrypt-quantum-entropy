### S03 — Structured logs, one error format and per-environment configuration

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — three small system-wide contracts that every module uses (agent 10–20 min)
**Builds:** Error format { error, message } and the code list; Logging: one JSON object per line, stderr for the MCP server, no values; Configuration: environment per process, profiles, .env.example; Time: all stored times UTC
**Extends:** none
**Stubs:** none
**Requirements:** CP-1, CT-4, DP-4, GC-6, OB-2, OB-3
**Guards:** CP-1, OB-3

#### Why
Every process logs the same way without ever logging a value, every refusal has the same two fields, and the same code runs in dev, staging and prod with only configuration changed.

#### How it's checked
The agent checks the log output, the error bodies for each code and the configuration behaviour with tests and commands.

#### Acceptance criteria
AC1. Every log line is structured JSON
   Given the stub HTTP service and stub MCP server each handle one request
   When their output is captured
   Then 100% of lines parse as JSON and carry timestamp, level and event
   Evidence: gate case `logs-are-json`
AC2. A log line carries the client id when it is known
   Given a request authenticated with a client key
   When the service logs the request
   Then the line has the client id and not the key
   Evidence: gate case `logs-carry-client-id`
AC3. The MCP server keeps standard output for the protocol
   Given the stub MCP server is started and its tools are listed
   When standard output is captured
   Then every line is a JSON-RPC message and all log lines are on standard error
   Evidence: `make mcp-list 2>/dev/null` shows protocol only
AC4. Registered secrets and values never reach a log line
   Given a secret and a value registered as markers
   When a log call includes fields named value, key, pepper, token and bytes holding the markers
   Then the written line holds redacted placeholders and neither marker
   Evidence: gate case `logs-scrub-markers`
AC5. Every error code has the same two fields
   Given a test route and test tool that raise each of invalid_argument, unauthorized, forbidden, not_found, budget_exhausted, temporarily_unavailable, upstream_misconfigured and database_unavailable
   When each is called
   Then every response is {error, message} with that snake_case code and no value in the message
   Evidence: gate case `error-codes` runs one request per code
AC6. The same code runs under different profiles
   Given profile settings for dev, staging and prod
   When the process starts under each profile
   Then it reads that profile's settings with 0 code changes
   Evidence: `APP_PROFILE=staging make config-show` prints non-secret settings per profile
AC7. A missing secret stops the start without printing values
   Given a required secret variable is unset
   When the process starts
   Then it exits non-zero naming the variable and prints no secret value
   Evidence: `env -u PEPPER make mcp-list; echo $?`
AC8. `.env.example` lists every variable with placeholders and no secret
   Given the settings the processes read
   When `make check-env` compares them with `.env.example` and the secret scan runs on it
   Then every variable is listed, every value is a placeholder and the scan finds 0 secrets
   Evidence: `make check-env`
AC9. Stored times are UTC
   Given the server's time zone is America/Chicago
   When the application writes a timestamp
   Then reading it back shows a +00 offset
   Evidence: gate case `times-are-utc`

#### Not in this story
- The per-call duration field on tool-call lines: picked up by S13
- Mapping of database outages to errors: picked up by S04
