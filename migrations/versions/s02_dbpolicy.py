"""Roles, column-level grants and guard triggers (S02). No password is set here (CP-1).

Roles can log in only after an operator sets a password from the environment (scripts/set_role_passwords.py).
Roles are cluster-wide, so creation tolerates a role that already exists and downgrade leaves roles in place.

Revision ID: s02_dbpolicy
Revises: 0001
"""

from alembic import op

revision = "s02_dbpolicy"
down_revision = "0001"
branch_labels = None
depends_on = None

ROLES = ("migrator", "mcp_server", "http_service", "backup")
APP_ROLES = ("mcp_server", "http_service", "backup")

# ARCHITECTURE.md "Database role grants". Delete, truncate and any other privilege are never granted.
GRANTS = [
    ("credentials", "mcp_server", "SELECT"),
    ("credentials", "http_service", "SELECT, INSERT, UPDATE (status, revoked_at)"),
    ("credentials", "backup", "SELECT"),
    ("budget", "mcp_server", "SELECT, UPDATE (used_kib, updated_at)"),
    ("budget", "http_service", "SELECT, UPDATE (cap_kib, updated_at)"),
    ("budget", "backup", "SELECT"),
    ("batches", "mcp_server", "SELECT, INSERT, UPDATE (outcome, settled_at)"),
    ("batches", "http_service", "SELECT"),
    ("batches", "backup", "SELECT"),
    ("issuances", "mcp_server", "INSERT"),
    ("issuances", "http_service", "SELECT"),
    ("issuances", "backup", "SELECT"),
    ("issuance_batches", "mcp_server", "INSERT"),
    ("issuance_batches", "http_service", "SELECT"),
    ("issuance_batches", "backup", "SELECT"),
    ("tokens", "mcp_server", "INSERT"),
    ("tokens", "http_service", "SELECT, UPDATE (used_at)"),
    ("tokens", "backup", "SELECT"),
    ("redemption_outcomes", "http_service", "SELECT, INSERT"),
    ("redemption_outcomes", "backup", "SELECT"),
    ("server_status", "mcp_server", "SELECT, INSERT, UPDATE"),
    ("server_status", "http_service", "SELECT"),
    ("server_status", "backup", "SELECT"),
]

CREATE_ROLE = """
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE {role} LOGIN;
    END IF;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$"""

METER_GUARD = """
CREATE FUNCTION guard_meter_only_rises() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.used_kib < OLD.used_kib THEN
        RAISE EXCEPTION 'budget.used_kib can only rise (% -> %)', OLD.used_kib, NEW.used_kib;
    END IF;
    RETURN NEW;
END $$"""

USED_FLAG_GUARD = """
CREATE FUNCTION guard_token_used_flag() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'used_at') IS DISTINCT FROM (to_jsonb(OLD) - 'used_at') THEN
        RAISE EXCEPTION 'tokens: only used_at can change';
    END IF;
    IF OLD.used_at IS NOT NULL AND NEW.used_at IS DISTINCT FROM OLD.used_at THEN
        RAISE EXCEPTION 'tokens.used_at is already set and cannot change';
    END IF;
    RETURN NEW;
END $$"""


def upgrade() -> None:
    for role in ROLES:
        op.execute(CREATE_ROLE.format(role=role))
    op.execute(METER_GUARD)
    op.execute("CREATE TRIGGER budget_meter_only_rises BEFORE UPDATE ON budget "
               "FOR EACH ROW EXECUTE FUNCTION guard_meter_only_rises()")  # fmt: skip
    op.execute(USED_FLAG_GUARD)
    op.execute("CREATE TRIGGER tokens_used_flag_once BEFORE UPDATE ON tokens "
               "FOR EACH ROW EXECUTE FUNCTION guard_token_used_flag()")  # fmt: skip
    for role in APP_ROLES:
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for table, role, privileges in GRANTS:
        op.execute(f"GRANT {privileges} ON {table} TO {role}")


def downgrade() -> None:
    for table, role, _ in GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
    op.execute("DROP TRIGGER tokens_used_flag_once ON tokens")
    op.execute("DROP TRIGGER budget_meter_only_rises ON budget")
    op.execute("DROP FUNCTION guard_token_used_flag()")
    op.execute("DROP FUNCTION guard_meter_only_rises()")
