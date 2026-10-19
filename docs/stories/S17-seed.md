### S17 — A seed script creates one admin, two clients and sample tokens without touching Qrypt

**Kind:** module
**Blocked by:** S05, S16
**Review gate:** agents
**Size:** S — one script over two finished interfaces; the blockers are real because the check is the rows both modules write (agent 5–10 min)
**Builds:** Bootstrap: seed script creates 1 admin, 2 clients, 3+ seed tokens
**Extends:** Access.createCredential (S05) and Tokens.storeSeedToken (S16)
**Stubs:** none
**Requirements:** DP-6, GC-4, GC-8
**Guards:** GC-4, GC-8

#### Why
A grader or the builder can start from a working database in one command, with keys printed once and no live entropy used.

#### How it's checked
The agent runs the script twice on an empty database and counts rows and the meter.

#### Acceptance criteria
AC1. An empty database gets 1 admin, 2 clients and 3 flagged tokens
   Given an empty migrated database
   When `make seed` runs
   Then there is 1 admin and 2 clients in credentials, at least 3 tokens with is_seed true and issuance_id null, and each key is printed once
   Evidence: `make seed && psql -c 'SELECT role, count(*) FROM credentials GROUP BY role'`
AC2. Running it twice causes no error and no duplicates
   Given the seed has run once
   When `make seed` runs again
   Then it exits 0, the counts stay 1, 2 and at least 3, and no key is printed
   Evidence: `make seed; echo $?`
AC3. The seed uses no live entropy
   Given network access blocked and the meter at 0
   When `make seed` runs
   Then it succeeds, the budget row exists with cap_kib 10240 and used_kib 0, and there are 0 batches rows
   Evidence: gate case `seed-no-entropy`
AC4. Seed tokens are stored through the interface
   Given a seed token value printed by the script
   When hashToken is called for its owner and value
   Then the result equals a token_hash in the tokens table
   Evidence: gate case `seed-token-hash`
AC5. The seed data is fictional and uses the pinned names [assumed]
   Given the seed script and its output
   When the names and owners are listed
   Then the admin is named admin, the clients billing-agent and support-agent, and the seed token owners seed-owner-1 to seed-owner-3, all synthetic
   Evidence: `make seed-list-names`

#### Not in this story
- Using the seed keys to connect: picked up by S20
