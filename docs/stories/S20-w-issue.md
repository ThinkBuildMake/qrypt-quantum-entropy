### S20 — Wire issuing values: connect with a real key, draw from the real pool and Qrypt, and get an audited value

**Kind:** wiring
**Blocked by:** S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17, S18, S19
**Review gate:** human demo
**Size:** L — one flow across six seams including first live contact with Qrypt; the trace carries three tools, so more criteria than a module story (agent 20–45 min)
**Wires:** D-1 Connect, D-2 get_random_bytes, D-3 random_int, D-4 generate_token, with D-13 Pool refill and D-14 Startup check inside the tool-call trace. Removes the stubs for Access.authenticate (in the MCP server and in the HTTP service, so every later wiring story runs on real keys), Pool.take, Meter.reserve and markBatch, Entropy source.fetch, Tokens.storeToken and the tool stubs.
**Builds:** none (joins modules; builds nothing new)
**Extends:** none
**Stubs:** none
**Requirements:** AZ-1, CO-1, CT-1, CT-2, DE-5, DP-8, FR-1.1, FR-1.2, FR-1.3, FR-1.4, FR-1.5, FR-1.6, FR-2.1, FR-3.1, FR-3.2, FR-3.3, GC-4, GC-6, PF-1, PF-3, RL-1, RL-2, RL-6, VR-6, VR-9
**Guards:** PF-1, PF-3, DE-1, GC-4, CP-2, CP-3, OB-3, RL-6

#### Why
Priya connects with her client key and gets random bytes, an integer and a token, each with its source and batch, from real entropy, with a plain error when something is wrong.

#### How it's checked
The human demo, run on the human's machine against Compose Postgres, with one short live run against Qrypt.

#### Acceptance criteria
AC1. The seed key connects and the call is attributed to its client
   Given `make seed` has run and the key printed for billing-agent is in the connection snippet
   When MCP Inspector connects with the snippet and get_random_bytes is called
   Then the tools are listed and the issuance row for the reference names billing-agent as the client
   Evidence: demo: Inspector with `docs/mcp-client.json`; `psql -c "SELECT c.name FROM issuances i JOIN credentials c ON c.id=i.client_id WHERE i.id='$REF'"`; integration case `connect-real-access`
AC2. A revoked key is refused where the stub accepted every key
   Given a throwaway client created and then revoked
   When MCP Inspector connects with its key
   Then the connection is refused with unauthorized and no tool is listed
   Evidence: demo; integration case `revoked-key-refused-real`
AC3. The hosted admin routes check real keys
   Given the seed admin key, the seed client key and no key
   When GET /admin/usage is called with each
   Then the responses are 200, 403 forbidden and 401 unauthorized
   Evidence: demo: three curls; integration case `http-real-keys`
AC4. Bytes come from the real pool with their source and batch
   Given the connected session and the mock Qrypt service
   When get_random_bytes is called with length 16 and encoding hex
   Then the value is 32 hex characters and the metadata has source qrypt, a batch_id that is a batches row of 64 KiB or more with outcome received, bytes_consumed 16 and a reference
   Evidence: demo; `psql -c "SELECT b.kib, b.outcome FROM batches b WHERE b.id='$BATCH'"`; integration case `issue-bytes-real-pool`
AC5. An integer comes back inside the range with its audit record
   Given the connected session
   When random_int is called with min 1 and max 6
   Then the value is an integer from 1 to 6 and the issuances row for the reference has tool random_int with byte_count values summing to bytes_consumed
   Evidence: demo; `psql -c "SELECT bytes_consumed FROM issuances WHERE id='$REF'"`; integration case `issue-int-real-pool`
AC6. A redeemable token is stored as a hash
   Given the connected session
   When generate_token is called with kind otp_6, owner user-42 and expiry_seconds 600
   Then the value is 6 digits, the tokens row for user-42 holds a hash and not the value, and its issuance_id equals the reference
   Evidence: demo; `psql -c "SELECT owner, issuance_id FROM tokens"` as the migrator; integration case `issue-token-real-store`
AC7. A refused token insert fails the whole call across the real seam
   Given an injected database fault on the tokens insert in the real store
   When generate_token is called with an owner
   Then the result is temporarily_unavailable with no value and no issuance row
   Evidence: demo; integration case `issue-token-store-fault`
AC8. Bad input is explained in plain words
   Given the connected session
   When get_random_bytes is called with length 0
   Then the result is invalid_argument naming length and no byte was drawn
   Evidence: demo; integration case `issue-invalid-no-draw`
AC9. A live run against Qrypt starts with a 1 KiB check and stays inside the budget
   Given the real Qrypt token in the environment and a meter at 0
   When the MCP server starts and one get_random_bytes call is made
   Then the batches table shows one startup_check of 1 KiB and one refill of 64 KiB or more, and used_kib is 1,024 or less
   Evidence: demo: `psql -c 'SELECT purpose, kib, outcome FROM batches; SELECT used_kib FROM budget'`; the human writes the reading in the deployment guide's budget log
AC10. Fail closed when Qrypt is down
   Given the mock returning 503, the pool drained and a client session
   When get_random_bytes is called
   Then within 2 s the result is temporarily_unavailable with no value and no new issuance row
   Evidence: demo; integration case `fail-closed-503`
AC11. The metrics report shows the numbers from this run
   Given the calls above and the saved server log
   When `make report LOG=logs/server.log` runs
   Then the p95, KiB per Qrypt request, Qrypt use against the cap and time to unavailable panels show numbers from this run
   Evidence: demo: `make report LOG=logs/server.log`

#### Not in this story
- The chat agent in front of these tools: picked up by S32
- Redeeming and tracing the token: picked up by S33
- Creating further clients and changing the budget: picked up by S34
- The status report behind /health: picked up by S35
- A 429 retry staying one batch and a second source flowing through to metadata and audit: picked up by S39, which adds them to the gate
