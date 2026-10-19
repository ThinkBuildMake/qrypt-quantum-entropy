"""Let the backup role read `alembic_version` (S31): pg_dump locks and dumps every table, and the restore needs it.

Revision ID: s31_backup_version
Revises: s02_dbpolicy
"""

from alembic import op

revision = "s31_backup_version"
down_revision = "s02_dbpolicy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON alembic_version TO backup")


def downgrade() -> None:
    op.execute("REVOKE ALL ON alembic_version FROM backup")
