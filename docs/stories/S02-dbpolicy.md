### S02 — Database roles, grants and guards enforce who can read and change what

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — four roles, a grant table across eight tables, three guards (agent 10–20 min)
**Builds:** Database roles and the column-level grant table; Trust boundary: mcp_server role cannot read or change written tokens and audit rows
**Extends:** none
**Stubs:** none
**Requirements:** AZ-3, CP-1, DP-3, FR-4.3, FR-5.4, RL-4, VR-4, VR-10
**Guards:** CP-1, DP-3

#### Why
Makes the database, not the application, stop the issuing role from reading tokens and anyone from changing an audit row, so a reviewer can trust the record.

#### How it's checked
An agent runs SQL as each role against the Compose Postgres 18 and sees the denials, and the gate holds the same cases.

#### Acceptance criteria
AC1. The issuing role cannot read tokens
   Given migrated database and a tokens row inserted by the migrator
   When `SELECT * FROM tokens` is run as mcp_server
   Then Postgres answers permission denied
   Evidence: `psql "$MCP_SERVER_URL" -c 'SELECT * FROM tokens'`
AC2. The issuing role can insert a token hash without reading it back
   Given migrated database
   When an INSERT into tokens without RETURNING is run as mcp_server
   Then one row exists when read as the migrator
   Evidence: `psql "$MCP_SERVER_URL" -c "INSERT INTO tokens (...) VALUES (...)"` then count as migrator
AC3. Audit rows cannot be updated or deleted by any application role
   Given an issuance and an issuance_batches row
   When UPDATE and DELETE are run on both tables as mcp_server, http_service and backup
   Then all twelve statements are denied
   Evidence: gate case `audit-append-only` prints 12 denials
AC4. The meter can only rise and only through the right columns
   Given budget row with used_kib 64
   When `UPDATE budget SET used_kib = 10` is run as mcp_server
   Then the guard rejects it and used_kib stays 64
   Evidence: `psql "$MCP_SERVER_URL" -c 'UPDATE budget SET used_kib = 10'`
AC5. The issuing role cannot change the cap
   Given budget row with cap_kib 10240
   When `UPDATE budget SET cap_kib = 1` is run as mcp_server
   Then Postgres answers permission denied
   Evidence: `psql "$MCP_SERVER_URL" -c 'UPDATE budget SET cap_kib = 1'`
AC6. A token's used flag can be set once
   Given a token with used_at null
   When http_service sets used_at
   Then the update succeeds
   Evidence: gate case `token-used-flag-set`
AC7. A set used flag cannot be cleared
   Given a token with used_at set
   When http_service sets used_at to null
   Then the guard rejects it and used_at is unchanged
   Evidence: gate case `token-used-flag-guard`
AC8. No role can delete or truncate any table
   Given all eight tables
   When DELETE and TRUNCATE are run on each as mcp_server, http_service and backup
   Then all 48 statements are denied
   Evidence: gate case `no-delete-truncate` prints 48 denials
AC9. The grants match the architecture's table and no password is in a migration file
   Given migrated database
   When the gate compares information_schema column privileges with the committed expected grants and greps migrations for password literals
   Then the comparison shows 0 differences and the grep finds 0 password literals
   Evidence: gate case `grants-match-architecture`; `git grep -n "PASSWORD '" -- migrations` prints nothing

#### Not in this story
- Seeding keys and tokens for the roles to use: picked up by S17
- The backup job that uses the backup role: picked up by S31
