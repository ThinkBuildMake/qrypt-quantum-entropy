### S24 — An admin sees use against the budget and per client, and sets the budget

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — two routes and two interface functions (agent 10–20 min)
**Builds:** getUsage GET /admin/usage; setBudget PUT /admin/budget; Meter.getUsage(); Meter.setBudget(capKib)
**Extends:** none
**Stubs:** Access.authenticate in the HTTP service (skeleton stub), removed by S20; the writers of the issuances it sums (skeleton stub), removed by S20
**Requirements:** AZ-1, CH-3, CT-2, FR-5.1, FR-5.4, FR-5.6
**Guards:** GC-4, FR-5.6

#### Why
Sam can see who drew how much and change the cap, with an empty valid view on first run.

#### How it's checked
The agent seeds issuances and calls both routes.

#### Acceptance criteria
AC1. Usage shows the cap, used KiB and bytes per client
   Given cap 10240, used 128, billing-agent with 900 bytes consumed and support-agent with 100
   When GET /admin/usage is called with an admin key
   Then the response is 200 with budget capKib 10240, usedKib 128 and perClient showing 900 and 100
   Evidence: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/usage`
AC2. An empty system shows an empty view
   Given a freshly migrated database with no clients or issuances
   When GET /admin/usage is called
   Then the response is 200 with capKib 10240, usedKib 0 and perClient empty
   Evidence: same curl
AC3. The budget can be set
   Given an admin key
   When PUT /admin/budget is called with {"capKib":500}
   Then the response is 200 with capKib 500 and usedKib unchanged
   Evidence: `curl -s -X PUT -H "Authorization: Bearer $ADMIN_KEY" -d '{"capKib":500}' $BASE/admin/budget`
AC4. Lowering the cap below use is accepted
   Given used 128
   When PUT /admin/budget is called with {"capKib":100}
   Then the response is 200 and GET /admin/usage shows capKib 100 and usedKib 128
   Evidence: gate case `budget-lowered-below-used` [assumed A-4]
AC5. Bad budget values are refused
   Given an admin key
   When PUT /admin/budget is called with -1, with 1.5 and with the text many
   Then each response is 400 with error invalid_argument
   Evidence: three curls
AC6. A client key or no key is refused on both routes
   Given a client key and no key
   When both routes are called
   Then the responses are 403 forbidden and 401 unauthorized
   Evidence: four curls
AC7. Daily per-client use derived from the audit rows equals the usage view
   Given a seeded day of issuances for two clients
   When the per-client sums are computed from the issuances rows alone and compared with perClient from GET /admin/usage
   Then the difference is 0 bytes for each client
   Evidence: gate case `usage-derivable-from-audit`

#### Not in this story
- Reserving budget: owned by S07
