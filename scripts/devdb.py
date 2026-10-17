"""Local development database helper behind `make db-up` / `make db-reset` when no Docker daemon is available.

  devdb.py up URL     create the database named in URL on its server if missing (waits for the server)
  devdb.py drop URL   drop it (used by db-reset), then `up` recreates it empty
URL's credentials must be allowed to CREATE DATABASE (the local superuser role, or the Compose user).
"""

import sys
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def _admin(url: str) -> str:
    return make_url(url).set(database="postgres").render_as_string(hide_password=False)


def _connect_admin(url: str, wait: float = 30.0) -> psycopg.Connection:
    deadline = time.monotonic() + wait
    while True:
        try:
            return psycopg.connect(_admin(url), autocommit=True)
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def main(action: str, url: str) -> int:
    name = make_url(url).database
    with _connect_admin(url) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if action == "up" and not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"created database {name}")
        elif action == "up":
            print(f"database {name} ready")
        elif action == "drop":
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            print(f"dropped database {name}")
        else:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]) if len(sys.argv) == 3 else 2)
