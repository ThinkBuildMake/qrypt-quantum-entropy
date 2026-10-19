### S23 — An admin looks up an issuance by reference and sees where it came from

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** S — one read route over four tables (agent 5–10 min)
**Builds:** getIssuance GET /admin/issuances/{reference}
**Extends:** none
**Stubs:** Access.authenticate in the HTTP service (skeleton stub), removed by S20; the writers of the rows it reads (Issuance, Tokens), removed by S20 and S33
**Requirements:** AZ-1, FR-4.1, FR-4.2, FR-4.3
**Guards:** CP-3

#### Why
Lets Priya answer a reviewer's question about one issued value without the record ever holding or showing the value.

#### How it's checked
The agent seeds an issuance with batches and a token and calls the route.

#### Acceptance criteria
AC1. A trace shows client, tool, size and batches
   Given a seeded issuance by billing-agent, tool get_random_bytes, 4 bytes, from two batches
   When GET /admin/issuances/{reference} is called with an admin key
   Then the response is 200 with reference, client id and name, tool, bytesConsumed 4, createdAt and two batches each with batchId, source, byteOffset, byteCount and requestedAt
   Evidence: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $BASE/admin/issuances/$REF`; `make trace REF=$REF` prints the same body
AC2. A token issuance shows the token summary without the hash or value
   Given a seeded issuance with a token for owner user-42, kind otp_6, redeemed
   When the same route is called
   Then the body has token owner, kind, expiresAt, usedAt and isSeed false and no hash or value
   Evidence: same curl
AC3. An issuance without a token shows token null
   Given a seeded issuance with no token
   When the same route is called
   Then token is null
   Evidence: same curl
AC4. An unknown reference is not found and a malformed one is invalid [assumed 400]
   Given no issuance with the uuid, and the text not-a-uuid
   When the route is called with each
   Then the responses are 404 not_found and 400 invalid_argument
   Evidence: two curls
AC5. A client key or no key is refused
   Given a client key and no key
   When the route is called
   Then the responses are 403 forbidden and 401 unauthorized
   Evidence: two curls
AC6. Records cannot be changed through the product [assumed 405]
   Given an issuance
   When PUT and DELETE are sent to the same path with an admin key
   Then both return 405
   Evidence: two curls

#### Not in this story
- Writing the rows it reads: picked up by S13 and S16
