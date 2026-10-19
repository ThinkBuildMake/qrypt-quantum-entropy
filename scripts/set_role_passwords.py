"""Set the login password of each application role from the environment (migrations never carry passwords, CP-1).

Run as the migrator against DATABASE_URL, after `make migrate`:
  MCP_SERVER_PASSWORD=... HTTP_SERVICE_PASSWORD=... BACKUP_PASSWORD=... python scripts/set_role_passwords.py
A role whose variable is unset is skipped.
"""

import os
import sys

import psycopg
from psycopg import sql

ROLES = {"mcp_server": "MCP_SERVER_PASSWORD", "http_service": "HTTP_SERVICE_PASSWORD", "backup": "BACKUP_PASSWORD"}


def main() -> int:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for role, variable in ROLES.items():
            if password := os.environ.get(variable):
                conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                print(f"password set for {role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
