"""Daily backup (RL-7): dump the database as the read-only `backup` role and encrypt it with `age` in one pipe.

  BACKUP_URL=postgresql://backup:...@host/db  BACKUP_AGE_RECIPIENT=age1...  python scripts/backup.py [OUT_DIR]

The recipient (public key) may instead sit in the file `backup/age-recipient.txt`. The dump goes from pg_dump straight
into age, so no unencrypted dump ever reaches the disk; the file appears under OUT_DIR (default `backups/`) only when
both tools succeeded. The connection string is handed to pg_dump through PG* environment variables and is never
printed: pg_dump's messages are redacted before they are shown. Needs `pg_dump` (same major version as the server)
and `age` on PATH.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

RECIPIENT_FILE = Path("backup/age-recipient.txt")
PG_ENV = {
    "host": "PGHOST", "port": "PGPORT", "user": "PGUSER", "password": "PGPASSWORD", "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
    "channel_binding": "PGCHANNELBINDING", "options": "PGOPTIONS", "target_session_attrs": "PGTARGETSESSIONATTRS",
    "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME",
}  # fmt: skip


def _redact(text: str, url: str) -> str:
    for secret in {url, *(str(v) for v in conninfo_to_dict(url).values())}:
        text = text.replace(secret, "***") if len(secret) > 2 else text
    return text


def _recipient_args() -> list[str]:
    if recipient := os.environ.get("BACKUP_AGE_RECIPIENT"):
        return ["-r", recipient]
    if RECIPIENT_FILE.is_file():
        return ["-R", str(RECIPIENT_FILE)]
    raise SystemExit(f"no public key: set BACKUP_AGE_RECIPIENT or create {RECIPIENT_FILE}")


def backup(url: str, out_dir: Path) -> Path:
    recipient = _recipient_args()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"qrypt-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump.age"
    partial = target.with_name(target.name + ".partial")
    params = conninfo_to_dict(url)
    if unknown := sorted(set(params) - set(PG_ENV)):
        raise SystemExit(f"BACKUP_URL has options this script does not pass on: {', '.join(unknown)}")
    pg_env = {**os.environ, **{PG_ENV[k]: str(v) for k, v in params.items()}}
    try:
        with partial.open("wb") as out:
            dump = subprocess.Popen(["pg_dump", "-Fc", "--no-owner", "--no-privileges"], env=pg_env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # fmt: skip
            age = subprocess.Popen(["age", *recipient], stdin=dump.stdout, stdout=out, stderr=subprocess.PIPE)
            dump.stdout.close()
            age_err = age.communicate()[1]
            dump_err = dump.stderr.read()
            dump.wait()
        if dump.returncode or age.returncode:
            sys.stderr.write(_redact((dump_err + age_err).decode(errors="replace"), url))
            raise SystemExit(f"backup failed (pg_dump exit {dump.returncode}, age exit {age.returncode})")
        partial.rename(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


if __name__ == "__main__":
    if not (backup_url := os.environ.get("BACKUP_URL")):
        raise SystemExit("BACKUP_URL is not set")
    print(f"backup written: {backup(backup_url, Path(sys.argv[1] if len(sys.argv) > 1 else 'backups'))}")
