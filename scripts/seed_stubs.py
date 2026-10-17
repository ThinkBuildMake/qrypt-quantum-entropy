"""Seed the stub principals and the stub pool batch for `make mcp-call` (local databases only; S20 removes)."""

import sys

import psycopg  # noqa: I001
from sqlalchemy.engine import make_url

from qrypt_entropy.config import get_settings
from qrypt_entropy.service.access import STUB_ADMIN_KEY, STUB_CLIENT_KEY, hash_key
from qrypt_entropy.stubs import seed_stub_batch

ROWS = [
    ("00000000-0000-4000-8000-0000000000c1", "stub-client", "client", STUB_CLIENT_KEY),
    ("00000000-0000-4000-8000-0000000000a1", "stub-admin", "admin", STUB_ADMIN_KEY),
]

if __name__ == "__main__":
    url = get_settings().database_url
    if make_url(url).host not in (None, "", "localhost", "127.0.0.1", "::1"):
        sys.exit("refusing to seed stub credentials into a non-local database")
    with psycopg.connect(url) as conn:
        for cid, name, role, key in ROWS:
            conn.execute(
                "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET key_hash = EXCLUDED.key_hash",
                (cid, name, role, hash_key(key)),
            )
    seed_stub_batch(url)
