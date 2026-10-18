"""S02: database roles, column grants and guard triggers, checked by running SQL as each role."""

import subprocess
import uuid

import psycopg
import pytest
from psycopg import errors, sql
from sqlalchemy.engine import make_url

from tests.conftest import GATE_STATS
from tests.expected_grants import EXPECTED, ROLES
from tests.test_migrations import migrate

TABLES = tuple(EXPECTED)
CLIENT, ISSUANCE, BATCH, TOKEN = (str(uuid.uuid4()) for _ in range(4))


def role_url(admin_url: str, role: str) -> str:
    return make_url(admin_url).set(username=role, password=f"{role}-test-only").render_as_string(hide_password=False)


@pytest.fixture
def db(empty_db):
    """Migrated database, a row in each audit table, a token and a meter at 64 KiB; the roles can log in."""
    assert migrate(empty_db).returncode == 0
    with psycopg.connect(empty_db, autocommit=True) as conn:
        for role in ROLES:
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(f"{role}-test-only"))
            )
        conn.execute("INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, 'c', 'client', 'k')", (CLIENT,))
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'random_int', 1)",
            (ISSUANCE, CLIENT),
        )
        conn.execute("INSERT INTO batches (id, purpose, kib) VALUES (%s, 'refill', 1)", (BATCH,))
        conn.execute("INSERT INTO issuance_batches VALUES (%s, %s, 0, 1)", (ISSUANCE, BATCH))
        conn.execute(
            "INSERT INTO tokens (id, issuance_id, owner, kind, token_hash, expires_at) "
            "VALUES (%s, %s, 'o', 'otp_6', 'h', now() + interval '1 hour')",
            (TOKEN, ISSUANCE),
        )
        conn.execute("UPDATE budget SET used_kib = 64")
    return empty_db


def as_role(db: str, role: str, statement: str, params=()):
    with psycopg.connect(role_url(db, role), autocommit=True) as conn:
        return conn.execute(statement, params).rowcount


def denied(db: str, role: str, statement: str, exc=errors.InsufficientPrivilege) -> bool:
    try:
        as_role(db, role, statement)
    except exc:
        return True
    return False


def scalar(db: str, query: str):
    with psycopg.connect(db) as conn:
        return conn.execute(query).fetchone()[0]


def test_ac1_issuing_role_cannot_read_tokens(db):
    assert denied(db, "mcp_server", "SELECT * FROM tokens")


def test_ac2_issuing_role_inserts_token_hash_without_reading_it_back(db):
    before = scalar(db, "SELECT count(*) FROM tokens")
    as_role(
        db,
        "mcp_server",
        "INSERT INTO tokens (id, owner, kind, token_hash, expires_at, is_seed) "
        "VALUES (gen_random_uuid(), 'o2', 'uuid_v4', 'h2', now() + interval '1 hour', true)",
    )
    assert scalar(db, "SELECT count(*) FROM tokens") == before + 1


AUDIT_STATEMENTS = (
    "UPDATE issuances SET bytes_consumed = 9",
    "DELETE FROM issuances",
    "UPDATE issuance_batches SET byte_count = 9",
    "DELETE FROM issuance_batches",
)


def test_ac3_audit_rows_cannot_be_updated_or_deleted_by_any_application_role(db):
    count = sum(denied(db, role, stmt) for role in ROLES for stmt in AUDIT_STATEMENTS)
    assert count == 12
    GATE_STATS["audit-append-only"] = f"{count} denials"
    assert scalar(db, "SELECT bytes_consumed FROM issuances") == 1
    assert scalar(db, "SELECT count(*) FROM issuance_batches") == 1


def test_ac4_meter_cannot_be_lowered(db):
    assert denied(db, "mcp_server", "UPDATE budget SET used_kib = 10", errors.RaiseException)
    assert scalar(db, "SELECT used_kib FROM budget") == 64
    assert as_role(db, "mcp_server", "UPDATE budget SET used_kib = 65") == 1


def test_ac5_issuing_role_cannot_change_the_cap(db):
    assert denied(db, "mcp_server", "UPDATE budget SET cap_kib = 1")
    assert scalar(db, "SELECT cap_kib FROM budget") == 10240


def test_ac6_used_flag_can_be_set_once(db):
    assert as_role(db, "http_service", "UPDATE tokens SET used_at = now()") == 1
    assert scalar(db, "SELECT used_at IS NOT NULL FROM tokens") is True
    GATE_STATS["token-used-flag-set"] = "ok"


def test_ac7_set_used_flag_cannot_be_cleared_or_moved(db):
    as_role(db, "http_service", "UPDATE tokens SET used_at = now()")
    for statement in ("UPDATE tokens SET used_at = NULL", "UPDATE tokens SET used_at = now() + interval '1 day'"):
        assert denied(db, "http_service", statement, errors.RaiseException)
    assert scalar(db, "SELECT used_at IS NOT NULL AND used_at <= now() FROM tokens") is True
    GATE_STATS["token-used-flag-guard"] = "ok"


def test_ac7_guard_also_binds_the_migrator_for_other_columns(db):
    with psycopg.connect(db, autocommit=True) as conn, pytest.raises(errors.RaiseException):
        conn.execute("UPDATE tokens SET owner = 'x'")


def test_ac8_no_role_can_delete_or_truncate_any_table(db):
    count = 0
    for role in ROLES:
        for table in TABLES:
            for stmt in (f"DELETE FROM {table}", f"TRUNCATE {table}"):
                assert denied(db, role, stmt), (role, stmt)
                count += 1
    assert count == 48
    GATE_STATS["no-delete-truncate"] = f"{count} denials"


def test_ac9_grants_match_the_architecture_table(db):
    with psycopg.connect(db) as conn:
        cols = {}
        for table, column in conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
        ):
            cols.setdefault(table, set()).add(column)
        actual = set(
            conn.execute(
                "SELECT grantee, table_name, column_name, privilege_type FROM information_schema.column_privileges "
                "WHERE table_schema = 'public' AND grantee = ANY(%s)",
                (list(ROLES),),
            ).fetchall()
        )
        table_level = set(
            conn.execute(
                "SELECT grantee, table_name, privilege_type FROM information_schema.table_privileges "
                "WHERE table_schema = 'public' AND grantee = ANY(%s) "
                "AND privilege_type NOT IN ('SELECT', 'INSERT', 'UPDATE')",
                (list(ROLES),),
            ).fetchall()
        )
    expected = {
        (role, table, column, privilege)
        for table, by_role in EXPECTED.items()
        for role, grants in by_role.items()
        for privilege, columns in grants.items()
        for column in (cols[table] if columns is None else columns)
    }
    expected.add(("backup", "alembic_version", "version_num", "SELECT"))  # S31: pg_dump needs it, see DEVIATIONS 15
    assert actual - expected == set()
    assert expected - actual == set()
    assert table_level == set()
    GATE_STATS["grants-match-architecture"] = "0 differences"


def test_ac9_no_password_literal_in_migrations():
    out = subprocess.run(
        ["git", "grep", "-n", "--untracked", "PASSWORD '", "--", "migrations"], capture_output=True, text=True
    )
    assert (out.returncode, out.stdout) == (1, "")


def test_migration_is_safe_to_run_twice_with_roles_already_present(db):
    assert migrate(db).returncode == 0
