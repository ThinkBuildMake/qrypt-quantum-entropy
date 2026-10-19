### S16 — Tokens are stored only as keyed hashes with owner and expiry

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — one table write path, one hash function and the constant-time rule (agent 10–20 min)
**Builds:** Tokens.storeToken(tx, issuanceId, owner, kind, value, expirySeconds); Tokens.storeSeedToken(owner, kind, value, expirySeconds); Tokens.hashToken(owner, value); tokens (write path); Only Tokens writes tokens and redemption_outcomes; seed goes through storeSeedToken; Hashes are compared in one place, in constant time (VR-5); The pepper and every plain value are absent from database, logs and outcomes (VR-6, CP-3, OB-6); A seed token row has is_seed true and no issuance_id (DP-6)
**Extends:** none
**Stubs:** Issuance (the caller of storeToken; skeleton stub), removed by S20
**Requirements:** CP-3, CT-2, DP-6, FR-2.6, RL-6, VR-5, VR-6
**Guards:** CP-1, CP-3

#### Why
Makes a stolen database useless for redeeming codes, and gives the seed script and the issuing path one place that hashes and stores them.

#### How it's checked
The agent stores tokens through the interface and searches database dumps for plain values and the pepper.

#### Acceptance criteria
AC1. The hash depends on owner and value
   Given owner user-42 and value 123456
   When hashToken is called for (user-42, 123456) twice and for (user-43, 123456)
   Then the first two hashes are equal and the third differs
   Evidence: gate case `hash-owner-bound`
AC2. The hash is HMAC-SHA-256 under the pepper
   Given owner user-42, value 123456 and a pepper
   When token_hash is compared with an independently computed HMAC-SHA-256 of the owner joined to the value, and then recomputed under a different pepper
   Then the first matches exactly and the second differs
   Evidence: gate case `hash-is-hmac-sha256`
AC3. Only the hash is stored
   Given a transaction opened by the caller
   When storeToken is called with owner user-42, kind otp_6, value 123456 and expiry 600
   Then the tokens row has the hash, owner, kind, expires_at, used_at null and is_seed false, and a dump of the database holds neither 123456 nor the pepper
   Evidence: `pg_dump "$DATABASE_URL" | grep -c 123456` prints 0
AC4. Storing commits or rolls back with the caller
   Given a caller transaction that rolls back after storeToken
   When the rows are counted
   Then there are 0 tokens rows
   Evidence: gate case `store-rolls-back`
AC5. Expiry is set on the database clock
   Given expiry 600 seconds
   When storeToken is called
   Then expires_at is within 1 s of the database time plus 600 s
   Evidence: gate case `store-db-clock`
AC6. A seed token is flagged and has no issuance
   Given a call to storeSeedToken
   When the row is read and then an insert with a null issuance_id and is_seed false is attempted
   Then the seed row has is_seed true and issuance_id null and the attempted insert is rejected by the database
   Evidence: gate case `seed-token-flag`
AC7. An owner over 256 characters is refused
   Given a 257-character owner
   When storeToken is called
   Then it is refused with invalid_argument and no row is written
   Evidence: gate case `store-owner-length`
AC8. Hashes are compared in one place, in constant time
   Given the codebase
   When the static check searches for token-hash comparisons
   Then exactly one comparison site exists and it is the constant-time one
   Evidence: gate case `single-constant-time-compare`

#### Not in this story
- Redeeming a token: picked up by S21
- The seed script that calls storeSeedToken: picked up by S17
