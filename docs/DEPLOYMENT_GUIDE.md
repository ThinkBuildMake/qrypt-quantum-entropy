# DEPLOYMENT_GUIDE.md — stand up the hosted service, the database and the backup on free tiers

This is the deployment guide the submission requires (DP-7). It covers GC-9 (free-tier host), DP-2 (a deployed URL for
at least 14 days), CO-1 (the Qrypt allotment), CP-4 (TLS), RL-7 (backup and restore) and the DB roles spike. It replaces the
scattered notes: `docs/DEPLOY.md` keeps the exact S01 check commands and the S20 live-run procedure, `docs/BACKUP.md` keeps the
backup internals. Where this guide and those differ, this guide is current. `make guide-check` fails if a command, script
or variable named here does not exist or if `.env.example` and the variable table below disagree.

**Status (2026-10-19): first deploy done.** The service runs on Render from `main` against the production database, the gate
is green on GitHub, and the first live Qrypt call has been made. The first-deploy checklist at the end shows what is ticked
and what is still open.

## 1. What you are deploying

| Piece | Where | Cost |
|---|---|---|
| HTTP service (`POST /verify`, the admin API, `GET /health`, `GET /metrics`) | Render free web service, `render.yaml` | $0 |
| Database | Render free Postgres 18 (`qrypt-db`), Neon free as the fallback | $0 |
| Daily backup | GitHub Actions `.github/workflows/backup.yml` | $0 |
| Agent (OpenRouter) | runs where you run it, not on Render | `:free` model |

The MCP server is a local stdio process (`make mcp-list`); only the HTTP service is hosted.

## 2. Free-tier limits to expect

- The free Render database lives **30 days** from creation, then is deleted 14 days later (STACK 8). One free database per workspace.
- There are **no platform backups** on the free database. Our own daily backup (section 7) is the only copy. Up to 24 hours of writes can be lost in a disaster (RL-7).
- The free web service **sleeps after 15 minutes idle** and takes **about 1 minute to wake**; the first request after idle is slow, later ones are normal. It may also restart at any time. Allow for this in scripted checks (retry `GET /health` for up to 90 seconds).
- Neon free scales to zero after 5 minutes idle, has 100 compute-hours a month, and has no scheduled backups.
- OpenRouter `:free` models are limited to 20 requests a minute and 50 a day, or 1,000 a day once the account has bought $10 of credit. The owner has bought the credit; confirm it shows with the `/api/v1/key` call named in STACK 19.
- Live Qrypt use is capped at **10,240 KiB** in total for the whole assessment (GC-4), and **each database has its own meter** (`budget.used_kib`): a new database starts its meter at 0, but the KiB spent against an earlier database still count against the 10,240. The budget log (section 8) is the only running total across databases.
- Plan for CO-1: development smoke tests up to 1,024 KiB, the recorded demo up to 512 KiB, the rest in reserve. Each restart of a live instance can cost up to the pool's high mark (128 KiB by default), so restart the live instance as little as possible.

## 3. Configuration by environment (dev, staging, prod)

One code base, three profiles, chosen by `APP_ENV` (`APP_PROFILE` is the same setting and wins). The profile only changes
defaults (dev logs at DEBUG to `logs/server.log`; staging and prod log at INFO to stdout); switching needs no code change (DP-4).
`make config-show` prints the effective settings with secrets redacted. `render.yaml` sets `APP_ENV=prod`. For a staging
service, copy the service in `render.yaml` under a new name with `APP_ENV=staging`; the free plan allows one free database,
so staging shares the database or uses Neon.

Secrets are set only in the host's environment store (Render: Environment tab; GitHub: repository secrets) or in a git-ignored
local `.env`. **Never commit `QRYPT_API_KEY`, `PEPPER`, `KEY_HASH_SECRET`, any role password, `OPENROUTER_API_KEY` or the age private key.** `make check-env` and `make secret-scan` (both in the gate) fail on a value in `.env.example` or a secret anywhere in the history.

### Environment variables

Every setting `.env.example` lists, in the same order. Secret means: set it in the host or your shell, never in a file in git.

| Variable | Secret | Purpose |
|---|---|---|
| `APP_ENV` | no | profile: dev, staging or prod |
| `DATABASE_URL` | yes | local default connection string (the database owner); each process uses its own role's string below when it is set |
| `MCP_SERVER_URL` | yes | connection string of the `mcp_server` role; required when `APP_ENV=prod` |
| `HTTP_SERVICE_URL` | yes | connection string of the `http_service` role; required when `APP_ENV=prod`, set in the Render dashboard |
| `MIGRATOR_URL` | yes | connection string of the database owner; read by migrations only (else `DATABASE_URL`); on Render it comes from the database |
| `LOG_LEVEL` | no | log level; the profile sets a default |
| `LOG_FILE` | no | log file path; empty means stdout |
| `MCP_CLIENT_KEY` | yes | client key the MCP server is launched with |
| `PER_CALL_MAX_BYTES` | no | largest request to the entropy tools |
| `POOL_LOW_MARK_BYTES` | no | pool level that triggers a refill |
| `POOL_HIGH_MARK_BYTES` | no | pool ceiling; the most a restart can discard |
| `TOKEN_EXPIRY_DEFAULT_SECONDS` | no | default token lifetime |
| `TOKEN_EXPIRY_MAX_SECONDS` | no | longest allowed token lifetime |
| `RATE_LIMIT_WINDOW_SECONDS` | no | failed-redemption window per owner |
| `RATE_LIMIT_LOCK_TIMEOUT_MS` | no | longest a `/verify` waits behind other attempts for one owner |
| `RATE_LIMIT_MAX_FAILURES` | no | failures allowed per owner in the window |
| `QRYPT_BASE_URL` | no | Qrypt endpoint; the mock's address in tests and demos |
| `QRYPT_TIMEOUT_SECONDS` | no | upstream request timeout |
| `QRYPT_API_KEY` | yes | Entropy-service access token |
| `PEPPER` | yes | keys token hashes; the service refuses to start without it |
| `KEY_HASH_SECRET` | yes | keys client-key hashes; required |
| `MCP_SERVER_PASSWORD` | yes | login password of the `mcp_server` role |
| `HTTP_SERVICE_PASSWORD` | yes | login password of the `http_service` role |
| `BACKUP_PASSWORD` | yes | login password of the `backup` role |
| `BACKUP_URL` | yes | connection string of the `backup` role (backup job secret) |
| `BACKUP_AGE_RECIPIENT` | no | age public key; or commit it as `backup/age-recipient.txt` |
| `AGE_IDENTITY` | yes | path of the offline private key, restore drill only |
| `OPENROUTER_API_KEY` | yes | the agent's OpenRouter token; never given to the MCP server |
| `OPENROUTER_BASE_URL` | no | leave at the default; tests and `make demo-chat` point it at a local stub |
| `AGENT_MODEL` | no | the `:free` model the agent calls |
| `AGENT_LOG_FILE` | no | agent log path |
| `AGENT_RESULTS_FILE` | no | where the agent writes its request count |

### Variables used only in commands

These appear in the commands below as shell variables you set yourself; they are not settings of the service.

| Variable | Meaning |
|---|---|
| `RENDER_URL` | the service's host name without scheme, e.g. `qrypt-entropy.onrender.com` |
| `RENDER_EXTERNAL_URL` | the database's external connection string from the Render dashboard |
| `RENDER_ADMIN_URL` | the same string; the database owner is the admin for the spike |
| `CLIENT_KEY` | a client key printed by `make seed` against that database |
| `ADMIN_KEY` | the admin key printed by `make seed` |
| `SPENT_KIB` | KiB already spent against earlier databases (the budget log's running total before the production database) |
| `CONFIRM_RESET` | set to 1 to let a demo reset an external database (never do this on the hosted one) |
| `BACKUP_DIR` | where `make backup` writes (default `backups`) |
| `BACKUP_FILE` | a specific backup file for the restore drill |
| `DRILL_ADMIN_URL` | a fresh Postgres server for the restore drill |

## 4. Deploy the web service and the spike database

Order matters because of the database's 30-day clock and the roles spike. Do steps 4 and 5 now as a rehearsal on a **spike
database**; create the production database in section 6.

### Step 4a: Render web service and Render Postgres (spike)

1. In Render choose New, Blueprint and point it at this repository. `render.yaml` creates the Render web service `qrypt-entropy` (free plan, Python 3.14, health check `/health`) and the Render Postgres `qrypt-db` (free, Postgres 18) and wires the database owner's string to the service as `MIGRATOR_URL`, which only the migration uses. The service itself connects as the least-privilege `http_service` role through `HTTP_SERVICE_URL`; it has no `DATABASE_URL` (a `prod` service refuses to start without `HTTP_SERVICE_URL`).
2. In the service's Environment tab fill the values `render.yaml` leaves empty (`sync: false`): `PEPPER`, `KEY_HASH_SECRET`, `HTTP_SERVICE_URL` (see step 4c), and add `QRYPT_API_KEY` only when you do the live run (section 8). Generate the first two with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Do not paste them anywhere else.
3. The start command runs `alembic upgrade head` as the owner (`MIGRATOR_URL`; safe to run twice) and then uvicorn; the free plan has no pre-deploy command, which is why migrations run at start. Schema is created only by migrations (DP-3).
4. TLS is terminated by Render; plain HTTP should be redirected to HTTPS. This has not been seen working (checklist item 3).

### Step 4b: the roles spike on the real database (S01, spike S-1)

The design depends on insert-only database roles (AZ-3, VR-10), and whether Render's free database lets you create roles is not
documented. Prove it before relying on it. The database owner's connection string is the admin connection:

```
make spike-roles DATABASE_URL=$RENDER_ADMIN_URL
```

Expect the four checks create, grant, insert and denied-select to pass; the script drops everything it created and prints no password.
It also runs locally (`make spike-roles` against the local server; it passed on Postgres 16). If it fails on Render, use the
Neon fallback below. The external connection string needs `?sslmode=require` if it does not already carry it.

### Step 4c: give the service its own role

The first deploy's migration creates the roles without a password, so the service cannot log in as `http_service` yet. Set the
password from your machine (`HTTP_SERVICE_PASSWORD=... DATABASE_URL=$RENDER_EXTERNAL_URL python scripts/set_role_passwords.py`),
then set `HTTP_SERVICE_URL` on the service to the database's connection string with the user `http_service` and that password
(and `?sslmode=require` if needed), and redeploy. Do the same with `MCP_SERVER_URL` for an MCP server that runs against this database.

### Step 4d: Neon fallback

If the spike fails on Render Postgres, create a Neon free project (Postgres, no expiry, no card), take its connection string
(include `sslmode=require`), set it as `MIGRATOR_URL` on the Render web service (replacing the `fromDatabase` value in the dashboard
or in `render.yaml`) and build `HTTP_SERVICE_URL` from it with the `http_service` role, and repeat `make spike-roles DATABASE_URL=...` against it. Neon scales to zero after 5 minutes idle (the
first query after that is slow), has no scheduled backups, and its compute-hours run out after 100 a month (then it suspends),
so the daily backup matters just as much. Record the choice in `docs/DEVIATIONS.md`.

## 5. Check the deployed service

Set `RENDER_URL`, and `CLIENT_KEY` and `ADMIN_KEY` from `make seed` run against that database (see the migrations step in section 6). Wake the service first; the first request can take about 1 minute.

```
curl -s https://$RENDER_URL/health
curl -sI http://$RENDER_URL/health
curl -s -X POST -H "Authorization: Bearer $CLIENT_KEY" -H 'Content-Type: application/json' -d '{"owner":"user-42","value":"123456"}' https://$RENDER_URL/verify
curl -s -H "Authorization: Bearer $ADMIN_KEY" https://$RENDER_URL/admin/usage
curl -s https://$RENDER_URL/admin/usage
```

Expect: `/health` answers; the plain-HTTP request is redirected to https (301 or 308) or refused; `/verify` answers with a verdict;
`/admin/usage` with the admin key shows the meter, and without a key answers 401. DP-2 asks for exactly this against all
three routes on submission day and on day 14.

## 6. Create the production database (on or after 2026-10-20)

The free database expires 30 days after creation and the deployed URL must stay reachable for at least 14 days after
submission (DP-2). So the production database is created **on or after 2026-10-20**, after **deleting the spike database**
(one free database per workspace, and the spike one would otherwise expire early).

1. Finish the spike (section 4) and write its meter reading in the budget log (section 8) if you made any live call.
2. Delete the spike database in the Render dashboard (and the Blueprint's `qrypt-db` with it). Do not delete it before you have noted anything you need from it.
3. On or after 2026-10-20 create the production database: sync the Blueprint again, or create a free Postgres 18 named `qrypt-db` and attach it to the service. Note the creation date; the database is deleted 30 days later and 14 days after that it cannot be recovered.
4. Apply the migrations from your machine with the external string, twice (the second run must change nothing), then set the role passwords:

   ```
   make migrate DATABASE_URL=$RENDER_EXTERNAL_URL
   make migrate DATABASE_URL=$RENDER_EXTERNAL_URL
   MCP_SERVER_PASSWORD=... HTTP_SERVICE_PASSWORD=... BACKUP_PASSWORD=... DATABASE_URL=$RENDER_EXTERNAL_URL python scripts/set_role_passwords.py
   ```
   Then seed the keys with `make seed DATABASE_URL=$RENDER_EXTERNAL_URL` (needs `KEY_HASH_SECRET` and `PEPPER` in your shell, the same values as the service) and keep the printed keys: they are shown once. Seeding uses 0 KiB of Qrypt entropy.
5. Re-run the checks in section 5. Production deployment itself is story S46.

Note the dates: on the creation day write it in the budget log; put "database expires" (creation + 30 days) in your calendar.

## 7. Backup and restore

Why: the free database has no backups. A GitHub Actions job, `.github/workflows/backup.yml`, runs **daily at 03:17 UTC**
(and on demand with "Run workflow"), dumps the database as the read-only `backup` role, encrypts the dump with `age` in the
same pipe (no plain dump touches a disk), and uploads the `.dump.age` file as an artifact kept for 30 days. The repository
is public, so the artifact is safe only because it is encrypted. The dump holds hashes and audit rows, never the pepper,
a key or a token value. Detail: `docs/BACKUP.md`.

One-time setup (a human, once):

1. Make the key pair on a trusted machine: `age-keygen -o backup-private.txt`.
2. Keep `backup-private.txt` **offline** (password manager, USB stick). Never commit it, never put it in a GitHub secret. Anyone with it can read every backup.
3. Commit the public key: `age-keygen -y backup-private.txt > backup/age-recipient.txt`. (Or set the `BACKUP_AGE_RECIPIENT` secret instead.)
4. Set the `backup` role's password (command in section 6) and add the repository secret named `BACKUP_URL` = `postgresql://backup:PASSWORD@HOST:5432/DB?sslmode=require`. The two secret names the job reads are `BACKUP_URL` and, if you did not commit the public key, `BACKUP_AGE_RECIPIENT`.
5. Run the workflow once and confirm an artifact appears. Point it at the production database after section 6. Re-point it (the secret) whenever the database is replaced, or it keeps dumping a deleted one.

Locally, with a throwaway key pair, the whole cycle runs with `python scripts/backup_demo.py`.

The restore drill proves a backup is usable. Do it once after the first scheduled run, with the artifact downloaded into `backups/`:

```
AGE_IDENTITY=backup-private.txt DATABASE_URL=$RENDER_EXTERNAL_URL make restore-drill
```

It reads the schema revision from the backup, builds a fresh empty database, runs the migrations to that revision, loads the
data, migrates to head, and compares row counts with `DATABASE_URL`; it exits 1 on any difference. Point it at a fresh
server with `DRILL_ADMIN_URL` (a `postgres:18` container) when you have Docker. A real restore (new database, same steps, then
the role passwords again) is in `docs/BACKUP.md`. The `pg_dump` major version must equal the server's (18 in CI).

## 8. Qrypt budget log

The live run, step by step, is in `docs/DEPLOY.md` ("the live run"). Before it: `psql "$DATABASE_URL" -c 'SELECT used_kib, cap_kib FROM budget'`
should show the meter of the database you are using; a **new database starts at 0 but earlier spend still counts against the
10,240 KiB**, so the running total below is what you check against the cap, not the meter. One live call costs the 1 KiB
startup check plus the first prefetch of 96 KiB (97 with the defaults; each server start spends about this much). The admin API can lower the cap (`PUT /admin/budget`),
for example to the headroom the log says remains. Never store a token value or key in this table.

Add one row for every database in use, with the KiB it has spent when you finish with it. Running total is the sum of the KiB spent column so far.

| Date | Database | KiB spent | Running total | Notes |
|---|---|---|---|---|
| 2026-10-17 | start of assessment | 0 | 0 | owner's reading, REQUIREMENTS GC-4 |
| 2026-10-19 | spike `qrypt-db` (Render, deleted) | 0 | 0 | roles spike only, no live call; production `qrypt-db` created 2026-10-19T20:53Z, expires 2026-11-18 |
| 2026-10-19 | production `qrypt-db` (Render, in use) | 97 | 97 | AC9 live run, one `get_random_bytes` (16 B): `startup_check` 1 KiB received; `refill` 96 KiB got HTTP 429 from Qrypt and is metered as failed (reservations are not refunded); no 401 |
| | | | | |

## 8b. Demos and checks that run locally

All of these use the mock Qrypt (0 KiB of live entropy) and a local database, never the hosted one. The demos reset the
database they run on (`make demo-guard` refuses an external `DATABASE_URL` without `CONFIRM_RESET=1`; never set that against
the hosted database).

| Target | What it shows |
|---|---|
| `make demo-issue` | issue a value end to end, with the evidence queries |
| `make demo-redeem` | issue, redeem and trace a token |
| `make demo-operate` | create and revoke clients, usage, the budget cap |
| `make demo-health` | health and metrics while the server stops and restarts (about 1 minute) |
| `make load` | both load runs (`make load-latency` for PF-1, `make load-concurrent` for PF-2 and PF-3) |
| `make fault-suite` | every Qrypt fault fails closed; `make two-process-budget` shows two processes share one cap |
| `make fuzz`, `make check-routes`, `make route-latency` | malformed input, the hosted routes, per-route p95 |
| `make backup`, `make restore-drill` | the backup and its drill (section 7) |
| `make report`, `make trace` | the nine report panels; the provenance of one issuance |

Run `make gate` for everything the CI gate runs.

## 8c. Checks against the deployed service, the production cap and the submission

All of these are read by the human with the service up. `CLIENT_KEY` and `ADMIN_KEY` come from `make seed`; the scripts read
them from the environment and never print them. Exit codes: 0 passed, 1 failed, 2 nothing failed but something could not be checked.

```
make check-deployed URL=https://$RENDER_URL
make check-routes URL=https://$RENDER_URL
```

`make check-deployed` calls `GET /health` (retrying for 90 seconds, because the free service sleeps), `POST /verify` and
`GET /admin/usage` over TLS, then asks for the plain `http://` address (it must be refused or redirected to https) and offers only TLS 1.1 (the
handshake must fail). `make check-routes URL=` calls every read-only hosted route once and fails on any entropy-like key or an
unexpected field; without `URL=` the same target runs the local test instead.

The production cap leaves room for what the earlier databases spent (X is the last running total in the table above, or
pass `SPENT_KIB`). Set it right after the production database is seeded, and read the final figure after the demo:

```
make budget-cap URL=https://$RENDER_URL SPENT_KIB=97
make budget-reading URL=https://$RENDER_URL SPENT_KIB=97
```

`make budget-cap` sets `capKib` to 10240 minus X and reads `GET /admin/usage` back (the cap must match and `usedKib` must be at or
below it). `make budget-reading` fails if `usedKib` plus X is over 10,240 and prints the row to paste into the log in section 8.

Last, `make submission-check` (required files, the one-line description, the full-history secret scan, the last push, one author, the
last commit against the 2026-10-21 04:59 UTC cut-off). The human steps in order are in `docs/SUBMISSION_CHECKLIST.md`.

## 9. Deviations the reader must know

Full list in `docs/DEVIATIONS.md`. The ones that matter when deploying:

- **Postgres 16 locally, 18 deployed.** The build sandbox had no Docker, so tests ran against a local Postgres 16 and the Compose Postgres 18 path (`make db-up` with Docker) is unexercised. Nothing in the schema needs 18-only features, but the first CI run (a `postgres:18` service) and the first Render deploy are the first time 18 is used.
- **No Docker here**, so `make db-up` falls back to one database per worktree on the local server, and `make restore-drill` restores into a throwaway local cluster by default.
- **Table ownership**: the role that runs the migrations owns the tables, not a separate `migrator` role (managed Postgres admins often cannot reassign ownership).
- **Secret scan**: `make secret-scan` uses a regex fallback where gitleaks is not installed; CI uses the real tool.

## 10. What was and was not verified here

Verified locally (Postgres 16, mock Qrypt): the migrations and running them twice, the roles spike, the routes through a local
uvicorn, the backup and the restore drill with a throwaway key pair, config profiles, the secret scan, `make guide-check`.

**Not verified: verify on first deploy.** Tick each, in order:

- [x] 1. Render builds from `render.yaml` on Python 3.14 with uv, and the service becomes healthy on `/health`. (2026-10-19)
- [x] 2. `make spike-roles` passes on the real Render free database (else take the Neon fallback). (2026-10-19, Render; no Neon)
- [x] 3. TLS: `https://$RENDER_URL/health` works and plain HTTP is redirected or refused. (2026-10-19: 301 to https; TLS 1.1 refused)
- [x] 4. The migrations apply on Postgres 18 and a second run changes nothing. (2026-10-19)
- [x] 5. A first request after idle takes about a minute and still succeeds; a restart of the service loses no rows (RL-7). (2026-10-19: after 17 minutes idle the first `/health` took 42 s and answered 200, the next 0.15 s; a Render restart left every table's row count unchanged)
- [x] 6. The first live Qrypt call: the startup check logs no 401, the meter reads 97 KiB or less (1 KiB startup check plus the 96 KiB first prefetch, section 8), and the budget log has its row. (2026-10-19: 97 KiB; the prefetch got a 429, see section 8)
- [x] 7. The gate workflow (`.github/workflows/gate.yml`) is green on GitHub, including the gitleaks run. (2026-10-19)
- [ ] 8. The backup workflow runs on its schedule, installs `pg_dump` 18 and `age`, and uploads an artifact that `make restore-drill` accepts. (2026-10-19: a manual run uploaded an artifact and the restore drill passed on it, every table matching; the first scheduled run, 2026-10-20 03:17 UTC, is still to confirm)
- [x] 9. The OpenRouter credit shows on the account and the `:free` model answers within the limits in section 2. (2026-10-19: credit confirmed with `/api/v1/key`; `google/gemma-4-31b-it:free` returned 429 from Google's shared free pool for over 15 minutes, so the agent runs on `minimax/minimax-m3.1-flash:free`, which answers tool calls in about 2 s; see `docs/DEVIATIONS.md`)
- [ ] 10. On day 14 the three routes (`/verify`, the admin API, `/health`) still answer from outside (DP-2).
