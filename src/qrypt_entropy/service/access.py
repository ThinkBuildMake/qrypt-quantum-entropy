"""Access: who may call what. Owns `credentials`. (Contract: ARCHITECTURE.md, Module: Access.)

Keys come from the operating system's random generator, never from Qrypt (GC-7). A key is stored only as an
HMAC-SHA-256 under the server-side secret `KEY_HASH_SECRET`; the plain key is returned once and never logged (AZ-5).
"""

import hashlib
import hmac
import secrets
import threading
import uuid
from contextlib import contextmanager

import psycopg
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import Forbidden, InvalidArgument, NotFound, TemporarilyUnavailable, Unauthorized
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Principal
from qrypt_entropy.storage.db import open_pool

log = get_logger("access")

ROLES = ("admin", "client")
_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


@contextmanager
def _connection():
    """A pooled connection; a database that cannot be reached becomes `temporarily_unavailable` (RL-7)."""
    url = get_settings().database_url
    try:
        with _pools_lock:
            if url not in _pools:
                _pools[url] = open_pool(url, timeout=5.0)
            pool = _pools[url]
        with pool.connection() as conn:
            yield conn
    except psycopg.OperationalError as err:
        log.error("database_unavailable", module="access", error_type=type(err).__name__)
        raise TemporarilyUnavailable("the credentials could not be read or written") from err


def hash_key(key: str) -> bytes:
    """The one function that turns a key into `key_hash`: HMAC-SHA-256 under the server-side secret."""
    secret = get_settings().key_hash_secret
    if not secret:
        log.error("key_hash_secret_missing")
        raise TemporarilyUnavailable("key hashing is not configured")
    return hmac.new(secret.encode(), key.encode(), hashlib.sha256).digest()


def authenticate(key: str | None, allowed_roles: set[str]) -> Principal:
    if not key:
        raise Unauthorized("missing, unknown or revoked key")
    key_hash = hash_key(key)
    with _connection() as conn:
        row = conn.execute("SELECT id, name, role, status FROM credentials WHERE key_hash = %s", (key_hash,)).fetchone()
    if row is None:
        log.warning("key_refused", reason="unknown")
        raise Unauthorized("missing, unknown or revoked key")
    client_id, name, role, state = str(row[0]), row[1], row[2], row[3]
    if state != "active":
        log.warning("key_refused", reason="revoked", client=name, client_id=client_id)
        raise Unauthorized("missing, unknown or revoked key")
    if role not in allowed_roles:
        raise Forbidden(f"the {role} role may not call this")
    return Principal(client_id, role, name)


def create_credential(role: str, name: str) -> tuple[Principal, str]:
    """Insert a credential and return it with its key, which is not recoverable afterwards."""
    if role not in ROLES:
        raise InvalidArgument("role must be admin or client")
    if not name.strip() or "\x00" in name:
        raise InvalidArgument("name must be non-empty text")
    key = secrets.token_urlsafe(32)  # operating-system randomness
    principal = Principal(str(uuid.uuid4()), role, name)
    try:
        with _connection() as conn:
            conn.execute(
                "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, %s, %s, %s)",
                (principal.client_id, name, role, hash_key(key)),
            )
    except UniqueViolation:
        raise InvalidArgument("a client with that name already exists") from None
    return principal, key


def create_client(name: str) -> dict:
    principal, key = create_credential("client", name)
    client = {"id": principal.client_id, "name": name, "role": "client", "status": "active"}
    return {"client": client, "key": key}


def revoke_client(client_id: str) -> dict:
    """Mark a client revoked; repeating it keeps the first `revoked_at`. Admin credentials are not clients."""
    with _connection() as conn:
        row = conn.execute(
            "UPDATE credentials SET status = 'revoked', revoked_at = COALESCE(revoked_at, now()) "
            "WHERE id = %s AND role = 'client' RETURNING id, name, role, status",
            (client_id,),
        ).fetchone()
    if row is None:
        raise NotFound("no such client")
    return {"client": {"id": str(row[0]), "name": row[1], "role": row[2], "status": row[3]}}
