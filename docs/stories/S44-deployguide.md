### S44 — Deployment guide for the free host and database

**Kind:** module
**Blocked by:** S31, S20
**Review gate:** agents
**Size:** M — one guide with a check that every command and variable in it exists (agent 10–20 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** CO-1, DP-2, DP-7, GC-9, RL-7
**Guards:** GC-2, GC-9, CP-4

#### Why
Lets someone else stand up the hosted service, the database and the backup on free tiers and know what to expect from them.

#### How it's checked
The agent runs the guide checks against the repository.

#### Acceptance criteria
AC1. The steps cover the web service, the database and the roles spike
   Given the guide
   When `make guide-check` reads it
   Then it has steps for the Render web service, the Render Postgres, the roles spike, the Neon fallback and the rule to create the production database on or after 2026-10-20 after deleting the spike one
   Evidence: `make guide-check`
AC2. Every variable in the guide is in `.env.example`
   Given the guide and `.env.example`
   When `make guide-check` compares them
   Then the sets agree
   Evidence: `make guide-check`
AC3. Every command in the guide exists
   Given the guide
   When `make guide-check` extracts the make targets it mentions
   Then each exists
   Evidence: `make guide-check`
AC4. Backup and restore steps are written
   Given the guide
   When it is read
   Then it covers making the key pair, keeping the private key offline, the secret names, the daily schedule and the restore drill
   Evidence: `make guide-check`
AC5. The free-tier limits and the Qrypt budget log are stated
   Given the guide
   When it is read
   Then it states the 30-day database life, no platform backups, sleep after 15 minutes idle with about 1 minute to wake, the OpenRouter limits, that each database has its own meter so KiB spent against an earlier database still count, and has a Qrypt budget log table with date, database, KiB spent and a running total
   Evidence: `make guide-check`

#### Not in this story
- Doing the production deployment: picked up by S46
