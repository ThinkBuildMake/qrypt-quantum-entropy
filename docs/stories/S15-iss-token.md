### S15 — generate_token issues an API key, a 6-digit code or a UUID, and can make it redeemable

**Kind:** module
**Blocked by:** S13
**Review gate:** agents
**Size:** M — three kinds, owner and expiry rules and the one-transaction guarantee (agent 10–20 min)
**Builds:** generate_token MCP tool; A redeemable token's issuance, batches and tokens rows are one transaction; byte_count values sum to bytes_consumed on every tool (CT-1); Token insert fails during issue: the whole issuance fails, no value (RL-6)
**Extends:** the audit transaction and byte accounting (S13)
**Stubs:** Access.authenticate, Pool.take and Tokens.storeToken (skeleton stubs), removed by S20
**Requirements:** CP-3, CT-1, CT-2, CT-3, FR-1.3, FR-1.5, FR-2.1, RL-6, VR-2, VR-3, VR-9
**Guards:** RL-6, CP-3, OB-3

#### Why
The agent developer gets a token in the format she asked for, and for an end user's code the hash, owner and expiry are stored in the same step.

#### How it's checked
The agent calls the tool for each kind against stubs and inspects the audit rows and the stubbed token store.

#### Acceptance criteria
AC1. A 6-digit code keeps leading zeros
   Given a stub pool whose draw maps to the number 7
   When generate_token is called with kind otp_6
   Then the value is 000007
   Evidence: gate case `token-otp-leading-zeros`
AC2. A 6-digit draw above the unbiased limit is rejected and counted
   Given a stub pool serving FF FF FF (16,777,215, above the limit of 16,000,000) and then 00 00 07
   When generate_token is called with kind otp_6
   Then the value is 000007 and bytes_consumed is 6
   Evidence: gate case `token-otp-rejection-counted`; the chi-square test of S14 runs on the same sampler
AC3. Every kind has its format
   Given a mocked uniform source
   When generate_token is called 10,000 times each for otp_6, uuid_v4 and api_key
   Then otp_6 is always 6 digits, uuid_v4 always has version nibble 4 and variant bits 10, and api_key is always 64 hex characters from 32 bytes
   Evidence: gate case `token-formats` [api_key encoding assumed hex]
AC4. A redeemable token stores owner and expiry with the issuance in one transaction
   Given an active client
   When generate_token is called with kind otp_6, owner user-42 and expiry_seconds 600
   Then the issuance row, its batch rows and the token store call with owner user-42 and 600 happen in one transaction
   Evidence: gate case `token-one-transaction`
AC5. Without an expiry the default of 600 seconds applies
   Given an owner and no expiry_seconds
   When generate_token is called
   Then the token is stored with a 600 second expiry
   Evidence: gate case `token-default-expiry`
AC6. Bad token input is refused before any byte is drawn
   Given the pool at 4096 bytes
   When generate_token is called with kind pin, with expiry_seconds 0, with expiry_seconds 86401 (the default maximum is 86,400 s [assumed]), with expiry_seconds and no owner, and with a 257-character owner
   Then each returns invalid_argument and the pool is unchanged
   Evidence: gate case `token-invalid`
AC7. A failed token insert fails the whole issuance
   Given an injected failure in the token store call
   When generate_token is called with an owner
   Then the result is temporarily_unavailable with no value and no issuance row
   Evidence: gate case `token-insert-fails`
AC8. byte_count always sums to bytes_consumed on all three tools
   Given calls to get_random_bytes, random_int and generate_token including rejected draws
   When each audit row is read
   Then the byte_count values of each issuance sum to its bytes_consumed
   Evidence: gate case `bytes-consumed-sums`
AC9. No issued value is in the audit tables
   Given 100 token calls
   When the issuances and issuance_batches tables are dumped and searched for each returned value in raw, hex and base64
   Then there are 0 matches
   Evidence: gate case `audit-has-no-values`

#### Not in this story
- The real token store and redemption: picked up by S16 and S21
