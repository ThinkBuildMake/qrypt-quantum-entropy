### S21 — A code redeems once, only before it expires, and every attempt is recorded

**Kind:** module
**Blocked by:** S16
**Review gate:** agents
**Size:** M — one route, one conditional update and one outcome table (agent 10–20 min)
**Builds:** verify POST /verify; Tokens.redeem(owner, value); redemption_outcomes (write path); A token redeems exactly once; two simultaneous redemptions give one redeemed (VR-4); Expiry set and checked on the database clock; redemption at or after expiry fails (FR-2.6); Two live tokens with the same owner and value are accepted; redemption consumes one at a time (A-7); Success metric record: redemption_outcomes outcome and attempted_at; Issued tokens redeemed before expiry: tokens created_at, expires_at, used_at, is_seed
**Extends:** Tokens.hashToken and the tokens write path (S16)
**Stubs:** Access.authenticate in the HTTP service (skeleton stub), removed by S20
**Requirements:** AZ-1, CP-3, CT-2, FR-2.4, FR-2.5, FR-2.6, OB-6, RL-5, VR-4
**Guards:** OB-3, CP-3, VR-6

#### Why
Lets the end user redeem a one-time code once, and records every attempt without the value so the success metric can be read.

#### How it's checked
The agent calls POST /verify against tokens stored through the store interface and reads redemption_outcomes.

#### Acceptance criteria
AC1. A valid code redeems
   Given a stored token for owner user-42 with value 123456 expiring in 600 s
   When POST /verify is called with a client key and {"owner":"user-42","value":"123456"}
   Then the response is 200 {"outcome":"redeemed"} and used_at is set
   Evidence: `curl -s -X POST -H "Authorization: Bearer $CLIENT_KEY" -d '{"owner":"user-42","value":"123456"}' $BASE/verify`
AC2. A second redemption is refused
   Given the token above already redeemed
   When POST /verify is called with the same body
   Then the response is 409 with error already_used and outcome already_used
   Evidence: same curl returns 409
AC3. A redemption at or after expiry is refused
   Given a token stored with a 2 s expiry and 3 s have passed
   When POST /verify is called with its owner and value
   Then the response is 410 with error expired and outcome expired
   Evidence: gate case `verify-expired`
AC4. A wrong value is unknown
   Given owner user-42 with a stored token
   When POST /verify is called with value 000000
   Then the response is 404 with error unknown and the outcome row has no token_id
   Evidence: same curl with the wrong value returns 404
AC5. Two simultaneous redemptions give exactly one success
   Given 1,000 trials, each a fresh token
   When two POST /verify calls with the same body are sent at once in each trial
   Then in every trial exactly one response is 200
   Evidence: gate case `verify-race-1000`
AC6. Each attempt records one outcome and never the value
   Given one attempt per outcome redeemed, already_used, expired and unknown
   When the redemption_outcomes rows are read and searched for the presented values
   Then there are 4 rows with the right outcomes, owner and time and 0 matches for the values
   Evidence: gate case `outcomes-recorded-no-value`
AC7. Malformed or unauthenticated requests record nothing [assumed P1]
   Given a body missing value, an owner of 257 characters and a call with no key
   When POST /verify is called with each
   Then the responses are 400, 400 and 401 and no outcome row is added
   Evidence: gate case `verify-invalid`
AC8. An admin key may also redeem, and two live tokens with the same value are consumed one at a time
   Given an admin key and two live tokens for one owner with the same value
   When POST /verify is called twice with the admin key
   Then both return 200 and a third returns 409 already_used
   Evidence: gate case `verify-admin-and-duplicates`

#### Not in this story
- The five-failures-per-ten-minutes limit and the 429 outcome: picked up by S22
