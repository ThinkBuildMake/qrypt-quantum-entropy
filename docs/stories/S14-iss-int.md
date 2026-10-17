### S14 — random_int returns an unbiased integer in the asked range

**Kind:** module
**Blocked by:** S13
**Review gate:** agents
**Size:** M — one tool with rejection sampling and a statistical proof (agent 10–20 min)
**Builds:** random_int MCP tool; random_int and otp_6 use rejection sampling, never a modulo (VR-2); A later draw fails in a multi-draw call: bytes discarded, no audit row
**Extends:** the audit transaction and byte accounting (S13)
**Stubs:** Access.authenticate and Pool.take (skeleton stubs), removed by S20
**Requirements:** CT-1, CT-2, FR-1.2, FR-1.5, VR-2, VR-3, VR-8
**Guards:** RL-6, CT-2

#### Why
The agent developer gets a fair random number between a minimum and a maximum, with no bias a reviewer could attack.

#### How it's checked
The agent runs the tool against a mocked uniform source, including a statistical test and a deliberately biased sampler that must fail it.

#### Acceptance criteria
AC1. Results stay inside the range
   Given a mocked uniform source
   When random_int is called 10,000 times with min 1 and max 6, and once with min 5 and max 5
   Then every result is between 1 and 6 and the last is 5
   Evidence: gate case `int-range`
AC2. The widest safe range works
   Given min -9007199254740991 and max 9007199254740991
   When random_int is called 10,000 times
   Then every result is an integer within the range
   Evidence: gate case `int-wide`
AC3. min above max or a range outside the safe-integer limit is refused
   Given the pool at 4096 bytes
   When random_int is called with min 7 and max 3, and with max 9007199254740992
   Then each returns invalid_argument and the pool is unchanged
   Evidence: gate case `int-invalid`
AC4. 1,000,000 draws are uniform
   Given a mocked uniform source
   When random_int(0, 999999) is run 1,000,000 times and a chi-square test on 1,000 equal bins is applied
   Then the test passes at alpha 0.001
   Evidence: gate case `int-chi-square`
AC5. The same test fails a modulo sampler
   Given a modulo-based sampler swapped in for random_int(0, 170) on single bytes
   When the same test runs
   Then it fails
   Evidence: gate case `int-mutation-modulo`
AC6. Rejected draws count as consumed
   Given a stub pool serving 0xFF then 0x05
   When random_int is called with min 0 and max 170
   Then the value is 5 and bytes_consumed is 2
   Evidence: gate case `int-rejection-counted`
AC7. A later draw that fails leaves no trace
   Given a stub pool serving the first draw and failing the second
   When random_int is called
   Then the result is temporarily_unavailable with no value and no issuance row
   Evidence: gate case `int-multidraw-failure`

#### Not in this story
- Token kinds: picked up by S15
