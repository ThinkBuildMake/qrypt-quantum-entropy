"""Access: who may call what. Owns `credentials`. (Contract: ARCHITECTURE.md, Module: Access.)

S01 STUB: two fixed keys, one admin and one client. Real keys, hashing and revocation: S05 and S06.
"""

import hashlib
import uuid

from qrypt_entropy.errors import Forbidden, Unauthorized
from qrypt_entropy.models import Principal
from qrypt_entropy.stubs import stub

STUB_CLIENT_KEY = "stub-client-key"
STUB_ADMIN_KEY = "stub-admin-key"
_STUB_PRINCIPALS = {
    STUB_CLIENT_KEY: Principal("00000000-0000-4000-8000-0000000000c1", "client", "stub-client"),
    STUB_ADMIN_KEY: Principal("00000000-0000-4000-8000-0000000000a1", "admin", "stub-admin"),
}


@stub("S05")
def authenticate(key: str | None, allowed_roles: set[str]) -> Principal:
    principal = _STUB_PRINCIPALS.get(key or "")
    if principal is None:
        raise Unauthorized("missing, unknown or revoked key")
    if principal.role not in allowed_roles:
        raise Forbidden(f"the {principal.role} role may not call this")
    return principal


@stub("S05")
def hash_key(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()  # real: HMAC-SHA-256 under a server-side secret


@stub("S05")
def create_credential(role: str, name: str) -> tuple[Principal, str]:
    return Principal(str(uuid.uuid4()), role, name), "stub-new-key"


@stub("S24")
def create_client(name: str) -> dict:
    return {
        "client": {"id": str(uuid.uuid4()), "name": name, "role": "client", "status": "active"},
        "key": "stub-new-key",
    }


@stub("S24")
def revoke_client(client_id: str) -> dict:
    return {"client": {"id": client_id, "name": "stub-client", "role": "client", "status": "revoked"}}
