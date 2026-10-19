# SUBMISSION_CHECKLIST.md — the steps only a human can do, in order

Deadline: the last commit must be at or before **2026-10-21 04:59 UTC** (2026-10-20 23:59 CDT). Details of each step are in
`docs/DEPLOYMENT_GUIDE.md`. Commands use shell variables you set yourself: `RENDER_URL` (host name without scheme),
`RENDER_EXTERNAL_URL` (the database's external string from the Render dashboard), and the keys `make seed` prints once
(`CLIENT_KEY`, `ADMIN_KEY`). Never commit a key, password or connection string.

Exit codes of the check scripts: 0 passed, 1 failed, 2 nothing failed but something could not be checked.

## A. The production database (on or after 2026-10-20)

- [ ] 1. Note the spike database's Qrypt spend (`psql "$RENDER_EXTERNAL_URL" -c 'SELECT used_kib, cap_kib FROM budget'`) and add its row to the budget log (guide section 8). That running total is X below.
- [ ] 2. Delete the spike database in the Render dashboard (one free database per workspace; the Render Postgres also expires 30 days after creation).
- [ ] 3. On or after 2026-10-20 create the production Render Postgres 18 (sync the Blueprint, guide section 6). Put its expiry (creation + 30 days) in your calendar.
- [ ] 4. Migrate twice (the second run changes nothing) and set the three role passwords:
  ```
  make migrate DATABASE_URL=$RENDER_EXTERNAL_URL
  make migrate DATABASE_URL=$RENDER_EXTERNAL_URL
  MCP_SERVER_PASSWORD=... HTTP_SERVICE_PASSWORD=... BACKUP_PASSWORD=... DATABASE_URL=$RENDER_EXTERNAL_URL python scripts/set_role_passwords.py
  ```
- [ ] 5. Seed (1 admin, 2 clients, 3 seed tokens) with the same `KEY_HASH_SECRET` and `PEPPER` as the service, and keep the printed keys:
  ```
  make seed DATABASE_URL=$RENDER_EXTERNAL_URL
  psql "$RENDER_EXTERNAL_URL" -c "SELECT role, count(*) FROM credentials GROUP BY role" -c "SELECT count(*) FROM tokens"
  ```
- [ ] 6. Set the secrets: on Render `PEPPER`, `KEY_HASH_SECRET`, `HTTP_SERVICE_URL` (user `http_service`, `?sslmode=require`) and, only for the live run, `QRYPT_API_KEY`; on GitHub the repository secret `BACKUP_URL` (user `backup`). Redeploy the service.
- [ ] 7. Set the production cap so earlier spend still counts (X from step 1):
  ```
  make budget-cap URL=https://$RENDER_URL SPENT_KIB=X
  ```

## B. The backup

- [ ] 8. Key pair once: `age-keygen -o backup-private.txt` (keep it offline), then `age-keygen -y backup-private.txt > backup/age-recipient.txt` and commit the public key only.
- [ ] 9. Run the backup workflow once and check the artifact is non-empty and a `.dump.age` file:
  ```
  gh workflow run backup
  gh run list --workflow backup --limit 3
  gh run download <run-id>
  ls -l backup-dump*/
  ```
- [ ] 10. Optional proof the backup restores: `AGE_IDENTITY=backup-private.txt DATABASE_URL=$RENDER_EXTERNAL_URL make restore-drill`.

## C. The deployed service

- [ ] 11. From a machine outside your network (the first request may take about 1 minute):
  ```
  make check-deployed URL=https://$RENDER_URL
  make check-routes URL=https://$RENDER_URL
  ```
  `check-deployed` must end `PASSED`. A `NOT CHECKED` line for TLS 1.1 means this machine's OpenSSL cannot offer it; run it from another machine, or `openssl s_client -tls1_1 -connect $RENDER_URL:443` where available.

## D. Evidence only you can produce

- [ ] 12. The real-model evaluation (OpenRouter `:free` model, credit bought): `export OPENROUTER_API_KEY=...` then `make eval-golden eval-adversarial eval-summary` (docs/DEPLOY.md, section S40).
- [ ] 13. Fold it into the results file and commit `results/`: `make results` then `make results-check`.
- [ ] 14. Record the demo video (the `demo-video` skill) against the deployed service; keep the live Qrypt spend within the plan in guide section 2.
- [ ] 15. After the demo, take the final reading and add its row to the budget log (guide section 8):
  ```
  make budget-reading URL=https://$RENDER_URL SPENT_KIB=X
  ```
- [ ] 16. Push, wait for the `gate` workflow to go green, and note the run URL (`gh run list --workflow gate --limit 1`) in the submission.

## E. Submission check (before the deadline)

- [ ] 17. Commit everything (the check fails on a dirty tree), push, then:
  ```
  make submission-check
  git log -1 --format=%cI
  ```
  It must end `PASSED`. `NOT CHECKED` for the last push means `gh` is missing, logged out or offline, or has no run for the last commit yet: look at the Actions tab yourself. A failing author line means the history has more than one author identity (GC-1 asks for one builder); rewrite or map the identities before submitting.

## F. After submission

- [ ] 18. Day 14 (14 days after submission): confirm the three routes still answer from outside, and that the database has not expired:
  ```
  make check-deployed URL=https://$RENDER_URL
  ```
