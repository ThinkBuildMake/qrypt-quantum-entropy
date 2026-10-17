"""Seed script (DP-6): 1 admin, 2 clients and 3 flagged seed tokens, with no live entropy and safe to run twice.

Writes only through Access.create_credential and Tokens.store_seed_token. Keys and token values come from the
operating system's random generator and are printed once, on the run that creates them. All names are synthetic.
Needs DATABASE_URL, KEY_HASH_SECRET and TOKEN_PEPPER in the environment. `list-names` prints what is stored.
"""

import secrets
import sys
import uuid

import psycopg

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access, tokens

CREDENTIALS = [("admin", "admin"), ("client", "billing-agent"), ("client", "support-agent")]
SEED_EXPIRY_SECONDS = 86400
SEED_TOKENS = [
    ("seed-owner-1", "otp_6", lambda: f"{secrets.randbelow(10**6):06d}"),
    ("seed-owner-2", "api_key", lambda: secrets.token_hex(16)),
    ("seed-owner-3", "uuid_v4", lambda: str(uuid.UUID(bytes=secrets.token_bytes(16), version=4))),
]


LOCK_ID = 1700017  # advisory lock so two concurrent seeds cannot both create the same rows


def _existing(query: str, params: tuple) -> bool:
    with psycopg.connect(get_settings().database_url) as conn:
        return conn.execute(query, params).fetchone() is not None


def seed() -> None:
    with psycopg.connect(get_settings().database_url) as lock_conn:
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))  # released when the connection closes
        _seed()


def _seed() -> None:
    for role, name in CREDENTIALS:
        row = None
        with psycopg.connect(get_settings().database_url) as conn:
            row = conn.execute("SELECT role FROM credentials WHERE name = %s", (name,)).fetchone()
        if row is not None:
            if row[0] != role:
                raise SystemExit(f"credential {name} exists with role {row[0]}, expected {role}")
            continue
        _, key = access.create_credential(role, name)
        print(f"credential role={role} name={name} key={key}")
    for owner, kind, make_value in SEED_TOKENS:
        if _existing("SELECT 1 FROM tokens WHERE owner = %s AND is_seed", (owner,)):
            continue
        value = make_value()
        tokens.store_seed_token(owner, kind, value, SEED_EXPIRY_SECONDS)
        print(f"seed_token owner={owner} kind={kind} value={value}")


def list_names() -> None:
    with psycopg.connect(get_settings().database_url) as conn:
        for role, name in conn.execute("SELECT role, name FROM credentials ORDER BY role, name"):
            print(f"{role} {name}")
        for (owner,) in conn.execute("SELECT owner FROM tokens WHERE is_seed ORDER BY owner"):
            print(f"seed-token-owner {owner}")


if __name__ == "__main__":
    try:
        list_names() if sys.argv[1:] == ["list-names"] else seed()
    finally:
        access.close_pools()
