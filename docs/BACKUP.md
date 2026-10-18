# BACKUP.md — daily encrypted backup and the restore drill (S31, RL-7)

The free database has no backups. A GitHub Actions job (`.github/workflows/backup.yml`, daily at 03:17 UTC) dumps it as the
read-only `backup` role, encrypts the dump with `age` in the same pipe (no plain dump ever touches a disk) and uploads the
`.dump.age` file as an artifact kept for 30 days. The repository is public, so the artifact is safe only because it is
encrypted; the dump holds client-key HMACs, token HMACs and audit rows, never the pepper, a key or a token value (CP-1, CP-3, VR-6).

## One-time setup (human, not done by the build)
1. Make the key pair on a trusted machine: `age-keygen -o backup-private.txt`. Keep that file **offline** (password manager, USB stick). Never commit it.
2. Commit the public key: `age-keygen -y backup-private.txt > backup/age-recipient.txt`.
3. Set the role password and the secret: `BACKUP_PASSWORD=... python scripts/set_role_passwords.py` (as the migrator), then in the repository settings add the secret `BACKUP_URL` = `postgresql://backup:PASSWORD@HOST:5432/DB?sslmode=require`.
4. Run the workflow once with "Run workflow" and confirm an artifact appears. Pointing it at the production database is S46.

## Run it locally
```
BACKUP_URL=postgresql://backup:PW@localhost:5432/qrypt BACKUP_AGE_RECIPIENT=age1... make backup     # writes backups/qrypt-<UTC>.dump.age
AGE_IDENTITY=backup-private.txt DATABASE_URL=postgresql://... make restore-drill                    # newest file in backups/, or BACKUP_FILE=
DATABASE_URL=postgresql://... python scripts/backup_demo.py                                          # whole thing with a throwaway key pair
```
`pg_dump` must be the same major version as the server (18 in CI; the workflow installs `postgresql-client-18`).

## What the restore drill does
Reads the schema revision out of the backup (the file is decrypted in a pipe: the plaintext dump never touches the disk),
starts from an empty database on a fresh server (a throwaway local cluster, or `DRILL_ADMIN_URL`, e.g. a `postgres:18`
container), runs the migrations up to that revision (a dump carries no roles, so this is what creates `migrator`,
`mcp_server`, `http_service`, `backup`, their grants and the guard triggers), loads the dump's data over those tables
with `age -d -i KEY FILE | pg_restore --data-only --disable-triggers -d URL`, then migrates to head (the catch-up when
the dump is older than the code), and compares the row count of every table with `SOURCE_URL`/`DATABASE_URL`.
It exits 1 if a count differs, a role is missing or the backup role's read-only grants did not come back.

## Restoring for real
Create a new database, find the dump's revision (`age -d -i KEY FILE | pg_restore --data-only -t alembic_version -f -`),
`alembic upgrade REVISION`, truncate the tables, then
`age -d -i KEY FILE | pg_restore --data-only --disable-triggers -d NEW_URL` as the database owner, then
`alembic upgrade head` and set the role passwords again.
