"""Show the backup and the drill working on DATABASE_URL with a throwaway key pair (needs the migrated database).

  DATABASE_URL=... python scripts/backup_demo.py     (run as the database owner; sets the backup role's password)
Runs `make backup`, lists backups/, opens the file with the right and a wrong key, tries an INSERT as the backup role
and runs `make restore-drill`. The throwaway keys and the backup file are removed at the end.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg import errors, sql
from sqlalchemy.engine import make_url


def sh(*cmd: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in (env or os.environ).items() if k != "UV"}
    return subprocess.run(cmd, env=env, text=True, capture_output=True, check=check)


def main() -> int:
    url = os.environ["DATABASE_URL"]
    password = "backup-test-only"  # noqa: S105  (same throwaway value as the tests: the role is cluster-wide)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE backup PASSWORD {}").format(sql.Literal(password)))
    backup_url = make_url(url).set(username="backup", password=password).render_as_string(hide_password=False)
    with tempfile.TemporaryDirectory() as tmp:
        keys = [Path(tmp) / "identity.txt", Path(tmp) / "wrong.txt"]
        for key in keys:
            sh("age-keygen", "-o", str(key))
        env = {
            **os.environ,
            "BACKUP_URL": backup_url,
            "BACKUP_AGE_RECIPIENT": sh("age-keygen", "-y", str(keys[0])).stdout.strip(),
        }
        Path("backups").mkdir(exist_ok=True)
        before = set(Path("backups").iterdir())
        print("$ make backup && ls backups/")
        print(sh("make", "backup", env=env).stdout.strip())
        made = sorted(set(Path("backups").iterdir()) - before)
        try:
            print(sh("ls", "-l", "backups/").stdout.strip())
            plain = [p.name for p in Path("backups").iterdir() if not p.name.endswith(".dump.age")]
            print(f"unencrypted files left in backups/: {plain}")
            right = subprocess.run(["age", "-d", "-i", str(keys[0]), str(made[-1])], capture_output=True)
            wrong = subprocess.run(["age", "-d", "-i", str(keys[1]), str(made[-1])], capture_output=True)
            print(f"right key: exit {right.returncode}, starts with {right.stdout[:5]!r}; "
                  f"wrong key: exit {wrong.returncode}, {len(wrong.stdout)} bytes out")  # fmt: skip
            try:
                with psycopg.connect(backup_url, autocommit=True) as conn:
                    conn.execute("INSERT INTO tokens (id) VALUES (gen_random_uuid())")
            except errors.InsufficientPrivilege as e:
                print(f"backup role INSERT INTO tokens: {type(e).__name__}: {str(e).splitlines()[0]}")
            print("$ make restore-drill")
            drill = sh(
                "make",
                "restore-drill",
                env={**env, "AGE_IDENTITY": str(keys[0]), "BACKUP_FILE": str(made[-1])},
                check=False,
            )
            print((drill.stdout + drill.stderr).strip())
            return drill.returncode
        finally:
            for p in made:
                p.unlink()


if __name__ == "__main__":
    sys.exit(main())
