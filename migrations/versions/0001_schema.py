"""Eight tables of ARCHITECTURE.md plus the one budget row (S01). Roles, grants and guards: S02.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE credentials (
        id          uuid PRIMARY KEY,
        name        text NOT NULL UNIQUE,
        role        text NOT NULL CHECK (role IN ('admin', 'client')),
        key_hash    bytea NOT NULL UNIQUE,
        status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
        created_at  timestamptz NOT NULL DEFAULT now(),
        revoked_at  timestamptz
    )""",
    """
    CREATE TABLE budget (
        id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        cap_kib     bigint NOT NULL DEFAULT 10240 CHECK (cap_kib >= 0),
        used_kib    bigint NOT NULL DEFAULT 0 CHECK (used_kib >= 0),
        updated_at  timestamptz NOT NULL DEFAULT now()
    )""",
    "INSERT INTO budget (id, cap_kib, used_kib) VALUES (1, 10240, 0)",
    """
    CREATE TABLE batches (
        id            uuid PRIMARY KEY,
        source        text NOT NULL DEFAULT 'qrypt',
        purpose       text NOT NULL CHECK (purpose IN ('refill', 'startup_check')),
        kib           integer NOT NULL CHECK (kib BETWEEN 1 AND 512),
        outcome       text NOT NULL DEFAULT 'requested' CHECK (outcome IN ('requested', 'received', 'failed')),
        requested_at  timestamptz NOT NULL DEFAULT now(),
        settled_at    timestamptz
    )""",
    """
    CREATE TABLE issuances (
        id              uuid PRIMARY KEY,
        client_id       uuid NOT NULL REFERENCES credentials (id),
        tool            text NOT NULL CHECK (tool IN ('get_random_bytes', 'random_int', 'generate_token')),
        bytes_consumed  integer NOT NULL CHECK (bytes_consumed > 0),
        created_at      timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX issuances_client_id_idx ON issuances (client_id)",
    """
    CREATE TABLE issuance_batches (
        issuance_id  uuid NOT NULL REFERENCES issuances (id),
        batch_id     uuid NOT NULL REFERENCES batches (id),
        byte_offset  integer NOT NULL,
        byte_count   integer NOT NULL CHECK (byte_count > 0),
        PRIMARY KEY (issuance_id, batch_id, byte_offset)
    )""",
    "CREATE INDEX issuance_batches_batch_id_idx ON issuance_batches (batch_id)",
    """
    CREATE TABLE tokens (
        id           uuid PRIMARY KEY,
        issuance_id  uuid REFERENCES issuances (id),
        owner        text NOT NULL CHECK (length(owner) <= 256),
        kind         text NOT NULL CHECK (kind IN ('api_key', 'otp_6', 'uuid_v4')),
        token_hash   bytea NOT NULL,
        expires_at   timestamptz NOT NULL,
        used_at      timestamptz,
        is_seed      boolean NOT NULL DEFAULT false,
        created_at   timestamptz NOT NULL DEFAULT now(),
        CHECK ((issuance_id IS NULL) = is_seed),
        CHECK (expires_at > created_at)
    )""",
    "CREATE UNIQUE INDEX tokens_issuance_id_key ON tokens (issuance_id) WHERE issuance_id IS NOT NULL",
    "CREATE INDEX tokens_owner_idx ON tokens (owner)",
    """
    CREATE TABLE redemption_outcomes (
        id            uuid PRIMARY KEY,
        token_id      uuid REFERENCES tokens (id),
        owner         text NOT NULL CHECK (length(owner) <= 256),
        outcome       text NOT NULL
            CHECK (outcome IN ('redeemed', 'already_used', 'expired', 'unknown', 'rate_limited')),
        attempted_at  timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX redemption_outcomes_owner_attempted_idx ON redemption_outcomes (owner, attempted_at)",
    "CREATE INDEX redemption_outcomes_attempted_idx ON redemption_outcomes (attempted_at)",
    """
    CREATE TABLE server_status (
        instance_id       uuid PRIMARY KEY,
        pool_level_bytes  integer NOT NULL,
        low_mark_bytes    integer NOT NULL,
        high_mark_bytes   integer NOT NULL,
        upstream_status   text NOT NULL
            CHECK (upstream_status IN ('ok', 'degraded', 'failing', 'misconfigured')),
        last_success_at   timestamptz,
        snapshot          jsonb NOT NULL,
        reported_at       timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX server_status_reported_at_idx ON server_status (reported_at DESC)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "server_status", "redemption_outcomes", "tokens", "issuance_batches",
        "issuances", "batches", "budget", "credentials",
    ):  # fmt: skip
        op.execute(f"DROP TABLE IF EXISTS {table}")
