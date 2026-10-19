### S31 — A daily encrypted database backup that has been restored once to prove it

**Kind:** module
**Blocked by:** S01, S02
**Review gate:** agents
**Size:** M — one scheduled job, one encryption step and one restore drill (agent 10–20 min)
**Builds:** Backup and restore (daily encrypted dump, tested restore, roles by migration re-run); Daily backup job (CI schedule, dump, encrypt, artifact)
**Extends:** the backup role (S02)
**Stubs:** none
**Requirements:** CP-1, RL-7, VR-6
**Guards:** CP-1, CP-3

#### Why
The free database has no backups, so a disaster loses at most the last day of records, and the dump is safe to keep in a public repository.

#### How it's checked
The agent runs the backup and a full restore drill locally with a test key pair.

#### Acceptance criteria
AC1. The backup produces an encrypted file and no plain dump
   Given a Compose database filled with synthetic rows by the test fixture and a test public key
   When `make backup` runs
   Then one encrypted file exists and no unencrypted dump remains on disk
   Evidence: `make backup && ls backups/`
AC2. Only the private key opens it
   Given the encrypted file
   When it is decrypted with the test private key and with a wrong key
   Then the first yields a valid dump and the second fails
   Evidence: gate case `backup-decrypt`
AC3. The dump holds no secret or value
   Given a database holding fixture tokens and known issued values
   When the decrypted dump is searched for the pepper, each plaintext client key and each token value
   Then there are 0 matches
   Evidence: gate case `backup-no-secrets`
AC4. A restore into a new Postgres 18 brings the data back
   Given a fresh Postgres 18 and the decrypted dump
   When `make restore-drill` restores it and re-runs the migrations
   Then the row count of every table equals the original and the roles exist again
   Evidence: `make restore-drill`
AC5. The backup role can only read
   Given the backup role's connection
   When an INSERT into tokens is attempted
   Then it is denied
   Evidence: `psql "$BACKUP_URL" -c "INSERT INTO tokens (id) VALUES (gen_random_uuid())"`
AC6. The schedule is daily and never prints the connection string
   Given the committed workflow file
   When the gate parses it
   Then it has a daily cron, uploads the artifact and echoes neither the connection string nor the dump
   Evidence: gate case `backup-workflow`

#### Not in this story
- Running the schedule against the production database: picked up by S46
