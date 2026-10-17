### S33 — Wire redeeming: a code works once, then not again, and can be traced to its batch

**Kind:** wiring
**Blocked by:** S20, S21, S22, S23
**Review gate:** human demo
**Size:** M — one token life across four seams (agent 10–20 min)
**Wires:** D-6 verify and D-7 getIssuance. Removes the stub token and issuance rows behind the HTTP service (the real Access.authenticate arrived in S20).
**Builds:** none (joins modules; builds nothing new)
**Extends:** none
**Stubs:** none
**Requirements:** AZ-1, AZ-3, AZ-6, CP-3, DP-8, FR-2.4, FR-2.5, FR-2.6, FR-4.1, FR-4.2, FR-4.3, OB-6, VR-10
**Guards:** AZ-3, VR-4, VR-6, CP-3, OB-6

#### Why
Jonas redeems his code once and cannot reuse it or use it late; an admin follows the same issuance back to its upstream batch, and the success metric shows real counts.

#### How it's checked
The human demo with curl against the real HTTP service and the tokens issued in the issue demo.

#### Acceptance criteria
AC1. A token issued by the MCP server redeems through the HTTP service
   Given a code issued with owner user-42 and expiry_seconds 600 by the real MCP server
   When POST /verify is called with the owner and the code
   Then the response is 200 with outcome redeemed
   Evidence: demo: `curl -s -X POST -H "Authorization: Bearer $CLIENT_KEY" -d '{"owner":"user-42","value":"<code>"}' $BASE/verify`; integration case `redeem-cross-process`
AC2. A second redemption is refused
   Given the code above already redeemed
   When POST /verify is called again
   Then the response is 409 already_used
   Evidence: demo; integration case `redeem-twice`
AC3. A redemption after expiry is refused
   Given a code issued with expiry_seconds 5 and 6 s have passed
   When POST /verify is called
   Then the response is 410 expired
   Evidence: demo; integration case `redeem-expired`
AC4. A wrong code is refused
   Given owner user-42
   When POST /verify is called with 000000
   Then the response is 404 unknown
   Evidence: demo
AC5. Five wrong tries lock the owner out
   Given owner user-42 with a live code and 5 wrong tries
   When POST /verify is called with the correct code
   Then the response is 429 rate_limited
   Evidence: demo; integration case `redeem-rate-limited`
AC6. An admin traces the issuance to its batch
   Given the reference in the metadata of the code issued above
   When GET /admin/issuances/{reference} is called with the admin key
   Then the body shows the client billing-agent, tool generate_token, bytesConsumed, the batch with source qrypt and its byteOffset, and the token with owner user-42, kind otp_6 and a usedAt time, with no code or hash
   Evidence: demo: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/issuances/$REF`; integration case `trace-real-rows`
AC7. A trace of an unknown reference or with the wrong key is refused
   Given the real credentials
   When GET /admin/issuances/{reference} is called for a random uuid with the admin key, and for the real reference with the client key
   Then the responses are 404 not_found and 403 forbidden
   Evidence: demo: two curls; integration case `trace-errors-real`
AC8. The database refuses to expose or alter the record to the issuing role
   Given the same database
   When the issuing role reads tokens and the HTTP role updates an issuance
   Then both are denied
   Evidence: demo: two `psql` commands; integration case `roles-across-processes`
AC9. The success and redeemed-before-expiry panels show the counts from this demo
   Given the attempts above
   When `make report` runs
   Then the success panel equals unknown over all attempts except rate_limited, computed from the outcome rows, and the redeemed panel shows the tokens redeemed over those issued that day
   Evidence: demo: `make report` and `psql -c 'SELECT outcome, count(*) FROM redemption_outcomes GROUP BY 1'`

#### Not in this story
- The chat that issued the code: shown in S32
