"""Tokens: redeemable tokens and redemption. Owns `tokens`, `redemption_outcomes`.

S16 builds the write path: `hash_token`, `store_token`, `store_seed_token` and the single constant-time hash
comparison `hashes_equal`. S21 builds `redeem`: a conditional UPDATE sets `used_at` only while the
token is unused and unexpired (database clock), so two simultaneous redemptions give one `redeemed` (VR-4).
S22 adds the rate limit.
Only a keyed hash of a token is ever stored; the plain value and the pepper are never written or logged.
"""

import hashlib
import hmac
import uuid

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError, InvalidArgument
from qrypt_entropy.storage import db

MAX_OWNER_LENGTH = 256


def hash_token(owner: str, value: str) -> bytes:
    """HMAC-SHA-256 under the server-side pepper of the owner joined to the value (so owners never collide)."""
    pepper = get_settings().token_pepper
    if pepper is None or not pepper.get_secret_value():
        raise AppError("token pepper is not configured", code="temporarily_unavailable")
    message = f"{owner}\0{value}".encode()
    return hmac.new(pepper.get_secret_value().encode(), message, hashlib.sha256).digest()


def hashes_equal(a: bytes, b: bytes) -> bool:
    """The one place token hashes are compared (VR-5)."""
    return hmac.compare_digest(a, b)


def _insert(conn, issuance_id: str | None, owner: str, kind: str, value: str, expiry_seconds: int) -> None:
    if len(owner) > MAX_OWNER_LENGTH:
        raise InvalidArgument(f"owner is longer than {MAX_OWNER_LENGTH} characters")
    if expiry_seconds <= 0:
        raise InvalidArgument("expiry_seconds must be positive")
    conn.execute(
        "INSERT INTO tokens (id, issuance_id, owner, kind, token_hash, expires_at, is_seed)"
        " VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s), %s)",
        (uuid.uuid4(), issuance_id, owner, kind, hash_token(owner, value), expiry_seconds, issuance_id is None),
    )


def store_token(tx, issuance_id: str, owner: str, kind: str, value: str, expiry_seconds: int) -> None:
    """Insert the token's hash on the caller's transaction `tx` (a psycopg connection); commit is the caller's."""
    _insert(tx, issuance_id, owner, kind, value, expiry_seconds)


def store_seed_token(owner: str, kind: str, value: str, expiry_seconds: int) -> None:
    """Seed script only: a token flagged is_seed with no issuance, committed on its own connection."""
    with db.connect() as conn:
        _insert(conn, None, owner, kind, value, expiry_seconds)


def redeem(owner: str, value: str) -> str:
    """Redeem the owner's token for `value`; record exactly one outcome (never the value) in the same transaction."""
    presented = hash_token(owner, value)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, token_hash FROM tokens WHERE owner = %s ORDER BY expires_at", (owner,)
        ).fetchall()
        matches = [row[0] for row in rows if hashes_equal(bytes(row[1]), presented)]
        token_id, outcome = None, "unknown"
        for candidate in matches:
            if conn.execute(
                "UPDATE tokens SET used_at = clock_timestamp()"
                " WHERE id = %s AND used_at IS NULL AND expires_at > clock_timestamp() RETURNING id",
                (candidate,),
            ).fetchone():
                token_id, outcome = candidate, "redeemed"
                break
        if matches and token_id is None:
            states = conn.execute(
                "SELECT id, used_at IS NOT NULL FROM tokens WHERE id = ANY(%s)", (matches,)
            ).fetchall()
            token_id, used = next((s for s in states if s[1]), states[0])
            outcome = "already_used" if used else "expired"
        conn.execute(
            "INSERT INTO redemption_outcomes (id, token_id, owner, outcome) VALUES (%s, %s, %s, %s)",
            (uuid.uuid4(), token_id, owner, outcome),
        )
    return outcome
