### S34 — Wire operating: create and revoke clients, see use per client, cap the budget

**Kind:** wiring
**Blocked by:** S20, S24
**Review gate:** human demo
**Size:** M — one operator flow across Access, Meter and the pool (agent 10–20 min)
**Wires:** D-8 getUsage, D-9 createClient and revokeClient, D-10 setBudget, with D-16 Budget alert inside the reserve path. Removes the stub usage rows behind the HTTP service (the real Access.authenticate arrived in S20).
**Builds:** none (joins modules; builds nothing new)
**Extends:** none
**Stubs:** none
**Requirements:** AZ-2, DP-8, FR-3.1, FR-3.2, FR-5.1, FR-5.2, FR-5.3, FR-5.4, FR-5.5, FR-5.6, GC-4, OB-5, VR-7
**Guards:** AZ-1, AZ-2, GC-4, RL-4

#### Why
Sam creates a client for a new agent, sees which client used how much, cuts one off, and sees the product refuse when the allotment is gone.

#### How it's checked
The human demo with curl for the admin routes and MCP Inspector for the client.

#### Acceptance criteria
AC1. An empty system shows an empty usage view
   Given a database reset with `make db-reset` and not yet seeded, then `make seed`
   When GET /admin/usage is called with the seed admin key
   Then the response is 200 with capKib 10240, usedKib 0 and perClient empty
   Evidence: demo: curl; integration case `usage-empty`
AC2. An admin creates a client and the client connects
   Given an admin key
   When POST /admin/clients creates reporting-agent and MCP Inspector connects with the returned key
   Then the tools are listed
   Evidence: demo: curl then Inspector; integration case `created-client-connects`
AC3. Usage shows bytes per client
   Given billing-agent and reporting-agent have each made calls
   When GET /admin/usage is called
   Then perClient shows each name with a bytesConsumed equal to the sum of bytes_consumed in its tool metadata, and usedKib equals the batches sum
   Evidence: demo: curl and `psql -c 'SELECT sum(kib) FROM batches'`; integration case `usage-matches-metadata`
AC4. A revoked client cannot connect
   Given reporting-agent revoked with POST /admin/clients/{id}/revoke
   When MCP Inspector connects with its key
   Then the connection is refused with unauthorized
   Evidence: demo; integration case `revoked-cannot-connect`
AC5. A revoked client's open session is refused on the next call
   Given an open session for support-agent and support-agent revoked
   When get_random_bytes is called on the open session
   Then the result is unauthorized and the pool level and used_kib are unchanged
   Evidence: demo; integration case `revoked-next-call`
AC6. A small budget refuses with a clear error and fails closed
   Given PUT /admin/budget set to 100 KiB and a pool with fewer bytes than the call needs
   When get_random_bytes is called until the budget is spent
   Then the result is budget_exhausted with no value, used_kib never passes 100 and the 80% and 100% events are each logged once
   Evidence: demo: curl, Inspector and `grep budget logs/server.log`; integration cases `budget-fail-closed` and `budget-alerts-real`
AC7. The cap-use panel shows the real reading
   Given the budget run above
   When `make report` runs
   Then the Qrypt-use-against-the-cap panel shows the KiB used and marks the 80% level
   Evidence: demo: `make report`

#### Not in this story
- The agent's wording when the budget is spent: shown in S32
