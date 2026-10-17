"""Spike S-1: can this Postgres create an insert-only role that works as the design needs? (`make spike-roles`)

Run as an admin against DATABASE_URL. Creates a scratch schema with a parent and a child table (child
references parent) and a throwaway role, then checks as that role:
  create         the role and tables could be created
  grant          INSERT-only grants on both tables worked
  insert         inserts without RETURNING succeed, including a child row whose parent the role cannot read
  denied-select  a SELECT as that role is refused
Everything it creates is dropped again. The role's password is random and never printed.
"""

import os
import secrets
import sys
import uuid

import psycopg
from psycopg import errors, sql
from sqlalchemy.engine import make_url

DEFAULT_URL = "postgresql://dev:dev@localhost:5432/postgres"


def main() -> int:
    admin_url = os.environ.get("DATABASE_URL") or DEFAULT_URL
    suffix = secrets.token_hex(4)
    schema, role = f"spike_{suffix}", f"spike_role_{suffix}"
    password = secrets.token_urlsafe(24)
    role_url = make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    ident = {"s": sql.Identifier(schema), "r": sql.Identifier(role)}
    failed = False

    def report(step: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        failed = failed or not ok
        print(f"{'PASS' if ok else 'FAIL'} {step}{' - ' + detail if detail else ''}")

    with psycopg.connect(admin_url, autocommit=True) as admin:
        try:
            try:
                admin.execute(sql.SQL("CREATE SCHEMA {s}").format(**ident))
                admin.execute(sql.SQL("CREATE TABLE {s}.parent (id uuid PRIMARY KEY)").format(**ident))
                admin.execute(
                    sql.SQL(
                        "CREATE TABLE {s}.child (id uuid PRIMARY KEY, parent_id uuid REFERENCES {s}.parent (id))"
                    ).format(**ident)
                )
                admin.execute(
                    sql.SQL("CREATE ROLE {r} LOGIN PASSWORD {p}").format(r=ident["r"], p=sql.Literal(password))
                )
                report("create", True)
            except psycopg.Error as err:
                report("create", False, type(err).__name__)
                return 1
            try:
                admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {s} TO {r}").format(**ident))
                admin.execute(sql.SQL("GRANT INSERT ON {s}.parent, {s}.child TO {r}").format(**ident))
                report("grant", True)
            except psycopg.Error as err:
                report("grant", False, type(err).__name__)
                return 1
            admin_parent = uuid.uuid4()
            admin.execute(sql.SQL("INSERT INTO {s}.parent VALUES (%s)").format(s=ident["s"]), (admin_parent,))
            try:
                with psycopg.connect(role_url, autocommit=True) as conn:
                    parent = uuid.uuid4()
                    conn.execute(sql.SQL("INSERT INTO {s}.parent (id) VALUES (%s)").format(s=ident["s"]), (parent,))
                    for parent_id in (parent, admin_parent):
                        q = sql.SQL("INSERT INTO {s}.child (id, parent_id) VALUES (%s, %s)").format(s=ident["s"])
                        conn.execute(q, (uuid.uuid4(), parent_id))
                    report("insert", True)
                    try:
                        conn.execute(sql.SQL("SELECT * FROM {s}.parent").format(s=ident["s"])).fetchall()
                        report("denied-select", False, "select was allowed")
                    except errors.InsufficientPrivilege:
                        report("denied-select", True)
            except psycopg.Error as err:
                report("insert", False, f"{type(err).__name__}: {str(err).splitlines()[0]}")
                report("denied-select", False, "not reached")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {s} CASCADE").format(s=ident["s"]))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {r}").format(r=ident["r"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
