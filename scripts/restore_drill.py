"""Restore drill (RL-7): prove the newest backup restores into a brand-new Postgres, then re-run the migrations.

  AGE_IDENTITY=path/to/private-key  [BACKUP_FILE=backups/x.dump.age]  [SOURCE_URL=...]  [DRILL_ADMIN_URL=...]
  python scripts/restore_drill.py

Steps: read the schema revision out of the backup (decrypted in a pipe: the plaintext dump never touches the disk);
start from an empty database on a fresh server (DRILL_ADMIN_URL, e.g. a `postgres:18` container or CI service; with
none set, a throwaway local cluster is created and removed); migrate it to that revision, which creates the roles,
grants and guards because a dump carries no roles; load the dump's data over those tables (as a superuser, with
triggers off); migrate to head (a no-op, or the catch-up for a dump older than the code); then compare the row count of
every table with SOURCE_URL (default DATABASE_URL) and check the roles and the backup role's read-only grants.
Exit 0 on success, 1 on any mismatch.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from glob import glob
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("migrator", "mcp_server", "http_service", "backup")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if done.returncode:
        raise SystemExit(f"{Path(cmd[0]).name} failed ({done.returncode}): {done.stderr.strip()[-2000:]}")
    return done


def _bin(name: str) -> str:
    versions = sorted(glob(f"/usr/lib/postgresql/*/bin/{name}"), key=lambda p: int(Path(p).parts[4]), reverse=True)
    # the newest installed version: a dump needs a server at least as new
    found = next(iter(versions), None) or shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found: set DRILL_ADMIN_URL to a fresh Postgres server instead")
    return found


@contextmanager
def throwaway_cluster():
    """A new local Postgres cluster (no roles of ours in it), listening on a free port; yields its admin URL."""
    root = Path(tempfile.mkdtemp(prefix="qrypt-drill-"))
    as_postgres = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_postgres:
        shutil.chown(root, user="postgres")
    data = root / "data"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    pg_ctl = _bin("pg_ctl")
    try:
        _run([*as_postgres, _bin("initdb"), "-D", str(data), "-U", "dev", "--auth=trust", "-E", "UTF8"])
        opts = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={root} -c fsync=off"
        _run([*as_postgres, pg_ctl, "-D", str(data), "-o", opts, "-w", "-l", str(root / "log"), "start"])
        yield f"postgresql://dev@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([*as_postgres, pg_ctl, "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def row_counts(url: str) -> dict[str, int]:
    with psycopg.connect(url) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
        return {
            t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0] for t in tables
        }


def migrate(url: str, revision: str) -> None:
    env = {**os.environ, "DATABASE_URL": url}
    _run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=ROOT, env=env)


def _decrypt(backup_file: Path, identity: Path) -> subprocess.Popen:
    return subprocess.Popen(["age", "-d", "-i", str(identity), str(backup_file)], stdout=subprocess.PIPE)


def _through_age(backup_file: Path, identity: Path, *cmd: str, **kw) -> subprocess.CompletedProcess:
    """Run cmd with the decrypted backup on its stdin; the plaintext only ever lives in the pipe."""
    age = _decrypt(backup_file, identity)
    try:
        return _run(list(cmd), stdin=age.stdout, **kw)
    finally:
        age.stdout.close()
        if age.wait():
            raise SystemExit("age could not decrypt the backup (wrong key?)")


def dump_revision(backup_file: Path, identity: Path) -> str:
    """The alembic revision stored in the backup (the data of its alembic_version table)."""
    out = _through_age(backup_file, identity, _bin("pg_restore"), "--data-only", "-t", "alembic_version", "-f", "-")
    rows = out.stdout.split("COPY public.alembic_version (version_num) FROM stdin;\n", 1)[1].split("\n\\.", 1)[0]
    return rows.strip()


def drill(backup_file: Path, identity: Path, source_url: str, admin_url: str) -> list[str]:
    """Run the drill against the server at admin_url; return the problems found (empty means it passed)."""
    name = "qrypt_restore_drill"
    target = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    revision = dump_revision(backup_file, identity)
    print(f"backup holds schema revision {revision}")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    migrate(target, revision)  # roles, grants and guards: a dump carries none of them
    with psycopg.connect(target, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
        conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(map(sql.Identifier, tables))))
    _through_age(
        backup_file, identity, _bin("pg_restore"), "--data-only", "--disable-triggers", "--no-owner", "-d", target
    )
    migrate(target, "head")
    problems = []
    original, restored = row_counts(source_url), row_counts(target)
    for table, count in original.items():
        print(f"table {table}: original {count}, restored {restored.get(table)}")
        if restored.get(table) != count:
            problems.append(f"row count of {table} differs")
    with psycopg.connect(target) as conn:
        have = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(ROLES),))}
        read, write = conn.execute(
            "SELECT has_table_privilege('backup','tokens','SELECT'), has_table_privilege('backup','tokens','INSERT')"
        ).fetchone()
    print(f"roles present: {sorted(have)}; backup can read tokens: {read}, can insert: {write}")
    if have != set(ROLES):
        problems.append(f"roles missing: {sorted(set(ROLES) - have)}")
    if not read or write:
        problems.append("the backup role's grants were not recreated")
    return problems


def main() -> int:
    files = sorted(Path("backups").glob("*.dump.age"))
    backup_file = Path(os.environ["BACKUP_FILE"]) if os.environ.get("BACKUP_FILE") else (files[-1] if files else None)
    if backup_file is None or not os.environ.get("AGE_IDENTITY"):
        raise SystemExit("need AGE_IDENTITY (private key file) and a backup in backups/ or BACKUP_FILE")
    source = os.environ.get("SOURCE_URL") or os.environ["DATABASE_URL"]
    admin = os.environ.get("DRILL_ADMIN_URL")
    if admin:
        problems = drill(backup_file, Path(os.environ["AGE_IDENTITY"]), source, admin)
    else:
        with throwaway_cluster() as admin:
            problems = drill(backup_file, Path(os.environ["AGE_IDENTITY"]), source, admin)
    print("restore drill FAILED: " + "; ".join(problems) if problems else "restore drill passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
