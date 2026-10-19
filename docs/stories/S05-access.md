### S05 — Admins create and revoke client keys; keys are stored only as hashes

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — two routes, three interface functions and one table write path (agent 10–20 min)
**Builds:** createClient POST /admin/clients; revokeClient POST /admin/clients/{id}/revoke; Access.authenticate(key, allowedRoles); Access.hashKey(key); Access.createCredential(role, name); credentials (write path); Only Access writes credentials; seed goes through createCredential; No plaintext key stored or logged (AZ-5); Key unknown or revoked: unauthorized, log event with client name, never the key; Authentication convention (Bearer key; MCP key from environment; /health open)
**Extends:** none
**Stubs:** Issuance tools (S01 stubs), removed by S20; the MCP server start that checks keys is S06
**Requirements:** AZ-1, AZ-2, AZ-5, FR-5.2, FR-5.3, GC-7
**Guards:** CP-1

#### Why
Lets an operator know who is drawing entropy and cut a client off, without ever storing a key in the clear.

#### How it's checked
The agent calls the two admin routes and inspects the database and logs.

#### Acceptance criteria
AC1. An admin creates a client and receives its key once
   Given an admin key and no client named billing-agent
   When POST /admin/clients is called with {"name":"billing-agent"}
   Then the response is 201 with client.role client, client.status active and a key
   Evidence: `curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" -d '{"name":"billing-agent"}' $BASE/admin/clients`
AC2. A duplicate or empty name is refused
   Given a client named billing-agent exists
   When POST /admin/clients is called again with the same name
   Then the response is 400 with error invalid_argument
   Evidence: `curl -s -o /dev/null -w '%{http_code}' ...` returns 400
AC3. The key is nowhere in storage or logs
   Given a client was created and its key noted
   When the database is dumped and the logs searched for that key
   Then there are 0 matches
   Evidence: `pg_dump "$DATABASE_URL" | grep -c "$KEY"` prints 0
AC4. An admin revokes a client
   Given an active client
   When POST /admin/clients/{id}/revoke is called
   Then the response is 200 with client.status revoked and the row has revoked_at set
   Evidence: `curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/clients/$ID/revoke`
AC5. Revoking an unknown client is a not-found
   Given no client with the id
   When POST /admin/clients/{id}/revoke is called
   Then the response is 404 with error not_found
   Evidence: `curl -s -o /dev/null -w '%{http_code}' ...` returns 404
AC6. An unknown or revoked key is not authenticated and the log names the client, not the key
   Given one unknown key and one revoked key
   When authenticate is called with each against the real credentials table
   Then both throw Unauthorized and the log line for the revoked one names the client and not the key
   Evidence: gate case `authenticate-unauthorized`
AC7. An admin key is refused where only clients are allowed and a client key is refused on admin routes
   Given one admin key and one client key
   When authenticate is called with allowedRoles client for the admin key and allowedRoles admin for the client key
   Then both throw Forbidden
   Evidence: gate case `authenticate-forbidden`
AC8. Keys come from the operating system, not Qrypt, and hash the same way every time
   Given the entropy source is replaced by one that fails the test if called
   When a client is created and hashKey is called twice with one key
   Then creation succeeds without calling the source and both hashes are equal
   Evidence: gate case `keys-from-os`

#### Not in this story
- Checking a key when an MCP session starts and on every tool call: picked up by S06
- Seed script that calls createCredential: picked up by S17
- Role matrix over every route: picked up by S37
