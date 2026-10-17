import os
import subprocess
import sys

import psycopg

TABLES = {
    "credentials", "budget", "batches", "issuances", "issuance_batches",
    "tokens", "redemption_outcomes", "server_status",
}  # fmt: skip


def migrate(url):
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True)


def test_ac6_migrate_twice_creates_eight_tables_and_budget_row(empty_db):
    assert migrate(empty_db).returncode == 0
    second = migrate(empty_db)
    assert second.returncode == 0, second.stderr
    with psycopg.connect(empty_db) as conn:
        names = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert TABLES <= names
        assert conn.execute("SELECT cap_kib, used_kib FROM budget").fetchall() == [(10240, 0)]
