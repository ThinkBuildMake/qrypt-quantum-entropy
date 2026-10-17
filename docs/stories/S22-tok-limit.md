### S22 — Five failed redemptions lock an owner out for ten minutes

**Kind:** module
**Blocked by:** S21
**Review gate:** agents
**Size:** S — one counter with a concurrency rule (agent 5–10 min)
**Builds:** After 5 failed attempts per owner in 10 minutes, rate_limited even with the right value (AZ-6); Under concurrent attempts, no more than 5 failures pass in a 10-minute window (AZ-6)
**Extends:** Tokens.redeem and the verify route (S21)
**Stubs:** none
**Requirements:** AZ-6
**Guards:** AZ-6

#### Why
Holds a guesser to 5 tries in 10 minutes against a 6-digit code, at the accepted cost that an attacker can lock an owner out for the window.

#### How it's checked
The agent sends failing and concurrent requests and reads the outcomes.

#### Acceptance criteria
AC1. The sixth attempt is refused even with the right value
   Given owner user-42 with a live token and 5 failed attempts in the window
   When POST /verify is called with the correct value
   Then the response is 429 with error rate_limited and the outcome row says rate_limited
   Evidence: gate case `verify-rate-limit`
AC2. The window ends
   Given the limit window configured at 3 s in the test and the owner locked out
   When 3 s pass and POST /verify is called with the correct value
   Then the response is 200
   Evidence: gate case `verify-rate-limit-window`
AC3. The limit is per owner
   Given owner user-42 locked out
   When POST /verify is called for owner user-43 with a correct value
   Then the response is 200
   Evidence: gate case `verify-rate-limit-per-owner`
AC4. Concurrent failures never pass more than five
   Given owner user-42 and 50 simultaneous wrong attempts
   When they are all sent
   Then at most 5 responses are failures other than 429 and the rest are 429
   Evidence: gate case `verify-rate-limit-concurrent`
AC5. A success does not count as a failure
   Given owner user-42 with 4 failed attempts and then a correct redemption that returned 200
   When a wrong attempt is made
   Then it returns 404 and not 429
   Evidence: gate case `verify-success-not-failure`

#### Not in this story
- Rate-limit presentation on a screen: the user sees 429 from the redeemer's own app
