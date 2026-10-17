### S13 — get_random_bytes returns a value with its source and writes the audit record first

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — the audit transaction, validation and the first tool (agent 10–20 min)
**Builds:** get_random_bytes MCP tool; Issuance.callStats(); issuances (write path); issuance_batches (write path); Only Issuance writes issuances and issuance_batches; One issuance row per successful call, committed before the value returns (VR-9, RL-6); The value and any pooled byte never appear in a log, error, audit row or other response (CP-3, OB-3); Invalid input is refused before any byte is drawn or KiB reserved (CT-2); Every tool call logs one structured line with its duration (OB-2); Pool cannot cover the call: temporarily_unavailable or budget_exhausted, OB-8 events; Audit write fails: temporarily_unavailable, no value; Invalid input: invalid_argument, log event; Tool-call time p95: duration on each tool-call log line
**Extends:** none
**Stubs:** Access.authenticate, Pool.take and Tokens.storeToken (skeleton stubs), removed by S20
**Requirements:** CP-3, CT-1, CT-2, CT-3, FR-1.1, FR-1.4, FR-1.5, FR-1.6, FR-5.5, OB-2, OB-4, PF-1, RL-3, RL-6, VR-9
**Guards:** CP-3, OB-3, RL-6, CT-2

#### Why
The agent developer gets random bytes in hex or base64 with proof of where they came from, and no value ever leaves without a durable record.

#### How it's checked
The agent calls the tool against stubbed Pool and Access and reads the audit rows.

#### Acceptance criteria
AC1. Hex output with metadata
   Given a stub pool serving bytes 00 01 02 03 from a seeded batch and an active client key
   When get_random_bytes is called with length 4 and encoding hex
   Then the value is 00010203 and the metadata has source qrypt, that batch_id, bytes_consumed 4 and a reference
   Evidence: `make mcp-call TOOL=get_random_bytes ARGS='{"length":4,"encoding":"hex"}'`
AC2. Base64 output
   Given the same stub pool serving bytes 00 01 02
   When get_random_bytes is called with length 3 and encoding base64
   Then the value is AAEC
   Evidence: gate case `bytes-base64`
AC3. One audit row is written and holds no value
   Given that call has returned
   When the issuances and issuance_batches rows for the reference are read
   Then there is 1 issuance with tool get_random_bytes and bytes_consumed 4, 1 batch row of count 4 and no column holds the value
   Evidence: `psql -c "SELECT * FROM issuances WHERE id='$REF'"`
AC4. A call spanning two batches lists both
   Given a stub pool serving 3 bytes of batch A then 2 bytes of batch B
   When get_random_bytes is called with length 5
   Then 2 issuance_batches rows exist and metadata.batch_ids lists A then B with batch_id A
   Evidence: gate case `bytes-two-batches`
AC5. Bad input is refused before any byte is drawn
   Given the pool at 4096 bytes and the meter at 64 KiB
   When get_random_bytes is called with length 0, length 1025 and encoding binary
   Then each returns invalid_argument naming the field and the pool and the meter are unchanged
   Evidence: gate case `bytes-invalid-input`
AC6. A pool failure returns an error and no value
   Given a stub pool that cannot cover the call
   When get_random_bytes is called
   Then the result is temporarily_unavailable or budget_exhausted with no value and no issuance row
   Evidence: gate case `bytes-pool-failure`
AC7. A failed audit write releases no value
   Given an injected database fault on the audit insert
   When get_random_bytes is called
   Then the result is temporarily_unavailable with no value and the drawn bytes are not given to the next call
   Evidence: gate case `bytes-audit-fault`
AC8. Each call logs one structured line with its duration, and call statistics count it
   Given three successful calls and one invalid call
   When the log and callStats are read
   Then there are 4 tool-call lines each with duration_ms and no value, and callStats shows 3 calls and 1 invalid_argument
   Evidence: gate case `bytes-log-and-stats`
AC9. The per-call maximum is configurable
   Given a per-call maximum of 64 bytes set in the environment
   When get_random_bytes is called with length 65
   Then the result is invalid_argument naming the maximum and the pool is unchanged
   Evidence: gate case `bytes-max-config`

#### Not in this story
- random_int and sampling: picked up by S14
- generate_token and the token row: picked up by S15
- Reading an issuance back over HTTP: picked up by S23
