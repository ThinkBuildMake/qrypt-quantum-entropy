### S46 — Production deployment and the submission checklist

**Kind:** module
**Blocked by:** S41, S42, S43, S44, S45, S31
**Review gate:** agents
**Size:** M — one deployment and one checklist; blocked outside the repository by the production database and secrets (agent 10–20 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** CO-1, CP-1, CP-4, DP-2, DP-5, DP-6, DP-7, GC-1, GC-4, GC-7, GC-9, RL-7
**Guards:** GC-1, GC-2, GC-4, GC-9, CP-1, CP-4

#### Why
Puts the finished system at a public https URL on free tiers with a seeded database, and checks every file the submission needs before the deadline.

#### How it's checked
The agent runs the deployed-URL and submission scripts against the production service after the owner has created it.

#### Acceptance criteria
AC1. The production database is migrated and seeded
   Given a Render Postgres created on or after 2026-10-20 and the spike database deleted
   When `make migrate` is run twice and `make seed` once against it
   Then the second migrate exits 0 and there are 1 admin, 2 clients and at least 3 seed tokens
   Evidence: `make migrate && make migrate && make seed` with the production URL
AC2. The three hosted routes answer from outside over TLS
   Given the deployed service
   When `make check-deployed` calls POST /verify, GET /admin/usage and GET /health from outside
   Then each answers with its contract shape
   Evidence: `make check-deployed URL=https://$RENDER_URL`
AC3. Plain HTTP and old TLS are refused
   Given the deployed service
   When `make check-deployed` requests http:// and TLS 1.1
   Then both are refused or redirected
   Evidence: `make check-deployed URL=https://$RENDER_URL`
AC4. No hosted route returns an entropy-derived value
   Given the deployed service
   When `make check-routes URL=https://$RENDER_URL` runs
   Then no response holds random bytes, an integer or an issued token
   Evidence: `make check-routes URL=https://$RENDER_URL`
AC5. The backup ran once on schedule or by hand
   Given the backup workflow on the repository
   When its latest run is listed
   Then one run succeeded and its artifact is non-empty and encrypted
   Evidence: `gh run list --workflow backup`; `gh run download`
AC6. The production cap leaves room for what was already spent
   Given the budget log in the deployment guide totalling X KiB spent against earlier databases
   When PUT /admin/budget is called on the production service with 10240 minus X
   Then GET /admin/usage shows capKib 10240 minus X and usedKib at or below it
   Evidence: `curl -s -X PUT -H "Authorization: Bearer $ADMIN_KEY" -d '{"capKib":<10240-X>}' $URL/admin/budget` then `curl -s ... $URL/admin/usage`
AC7. The final live reading is recorded
   Given the production service after the demo
   When GET /admin/usage is called
   Then usedKib plus X is 10,240 or less and the reading is added to the budget log
   Evidence: `curl -s -H "Authorization: Bearer $ADMIN_KEY" $URL/admin/usage`
AC8. The submission checklist passes
   Given the repository at its last commit
   When `make submission-check` runs
   Then source, README, .env.example, mock upstream, snippet, technical docs, deployment guide, results file and AI_USAGE.md are present, the one-line description is 120 characters or fewer, the full-history secret scan finds 0 secrets, the last push is green, and there is 1 author
   Evidence: `make submission-check`
AC9. The last commit is inside the deadline
   Given the last commit's timestamp
   When `make submission-check` compares it with 2026-10-21 04:59 UTC
   Then it is at or before the cut-off
   Evidence: `git log -1 --format=%cI`

#### Not in this story
- The demo video: made later by the demo-video skill
