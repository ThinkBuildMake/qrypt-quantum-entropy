### S06 — A client connects over stdio only with an active client key

**Kind:** module
**Blocked by:** S05
**Review gate:** agents
**Size:** S — one startup check and one per-call check (agent 5–10 min)
**Builds:** connect (stdio MCP session, client key in server environment); A failed authentication draws 0 bytes and uses 0 KiB (AZ-2)
**Extends:** Access.authenticate and the credentials write path (S05)
**Stubs:** Issuance tools (S01 stubs), removed by S20; Pool and Meter (stubs) for the bytes-unchanged check, removed by S20
**Requirements:** AZ-1, AZ-2, FR-3.1, FR-3.2, FR-5.3
**Guards:** AZ-2

#### Why
An agent developer sees the tools only with a valid client key, and a refused connection says why.

#### How it's checked
The agent starts the MCP server with each kind of key and checks the result.

#### Acceptance criteria
AC1. An active client key connects and sees the tools
   Given an active client key in the MCP server's environment
   When an MCP initialize request is made
   Then the tool list holds get_random_bytes, random_int and generate_token
   Evidence: `CLIENT_KEY=$KEY make mcp-list`
AC2. A missing key is refused with a clear message
   Given no client key in the environment
   When an MCP initialize request is made
   Then it is refused with unauthorized naming the missing key and no tool is listed
   Evidence: `env -u CLIENT_KEY make mcp-list`
AC3. An unknown key is refused
   Given a key that matches no credential
   When an MCP initialize request is made
   Then it is refused with unauthorized and no tool is listed
   Evidence: `CLIENT_KEY=nope make mcp-list`
AC4. A revoked key is refused
   Given a client key whose client was revoked
   When an MCP initialize request is made
   Then it is refused with unauthorized and no tool is listed
   Evidence: revoke then `CLIENT_KEY=$REVOKED make mcp-list`
AC5. An admin key is refused with forbidden
   Given an admin key in the environment
   When an MCP initialize request is made
   Then it is refused with forbidden and no tool is listed
   Evidence: `CLIENT_KEY=$ADMIN_KEY make mcp-list`
AC6. A revocation takes effect on the very next call
   Given a connected session, a pool level of 4096 bytes, the meter at 64 KiB and the client revoked since
   When a tool is called on the open session
   Then the call returns unauthorized and the pool level and the meter are unchanged
   Evidence: gate case `revoke-next-call`
AC7. A call with a bad key draws nothing
   Given an unknown key on a tool call
   When get_random_bytes is called
   Then the result is unauthorized, the pool level is unchanged and 0 KiB were reserved
   Evidence: gate case `bad-key-draws-nothing`

#### Not in this story
- The real tools behind the connection: picked up by S13, S14 and S15
