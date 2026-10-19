"""Tokens: redeemable tokens and redemption. Owns `tokens`, `redemption_outcomes`.

S16 builds the write path: `hash_token`, `store_token`, `store_seed_token` and the single constant-time hash
comparison `hashes_equal`. S21 builds `redeem`: a conditional UPDATE sets `used_at` only while the
token is unused and unexpired (database clock), so two simultaneous redemptions give one `redeemed` (VR-4).
S22 adds the rate limit (AZ-6): `redeem` first takes a per-owner transaction advisory lock, so attempts for one owner
run one at a time, then counts the owner's failed outcomes (`already_used`, `expired`, `unknown`) in the window on the
database clock; at the limit the attempt is recorded as `rate_limited` and nothing else is looked at.
Only a keyed hash of a token is ever stored; the plain value and the pepper are never written or logged.
"""

import hashlib
import hmac
import threading
import time
import uuid
from collections import deque

import psycopg

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, NotFound, RateLimited, TemporarilyUnavailable
from qrypt_entropy.storage import db

MAX_OWNER_LENGTH = 256
FAILED_OUTCOMES = ("already_used", "expired", "unknown")


def check_owner(owner: str) -> None:
    """Reject an owner the database cannot hold (NUL) or that is not valid text (a lone surrogate)."""
    try:
        owner.encode()
    except UnicodeEncodeError:
        raise InvalidArgument("owner must be valid text") from None
    if "\0" in owner:
        raise InvalidArgument("owner must not contain a NUL character")


def hash_token(owner: str, value: str) -> bytes:
    """HMAC-SHA-256 under the server-side pepper of the owner joined to the value (so owners never collide)."""
    pepper = get_settings().token_pepper
    if pepper is None or not pepper.get_secret_value():
        raise AppError("token pepper is not configured", code="temporarily_unavailable")
    try:
        message = f"{owner}\0{value}".encode()
    except UnicodeEncodeError:
        raise InvalidArgument("owner and value must be valid text") from None
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


def _lock_owner(conn, owner: str, timeout_ms: int) -> None:
    """Serialise attempts for one owner; a flood cannot hold pooled connections waiting for longer than the timeout."""
    conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{timeout_ms}ms",))
    try:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,))
    except psycopg.errors.LockNotAvailable:
        raise TemporarilyUnavailable("too many simultaneous attempts for this owner") from None
    conn.execute("SELECT set_config('lock_timeout', '0', true)")


def _record(conn, token_id, owner: str, outcome: str) -> str:
    conn.execute(
        "INSERT INTO redemption_outcomes (id, token_id, owner, outcome, attempted_at)"
        " VALUES (%s, %s, %s, %s, clock_timestamp())",
        (uuid.uuid4(), token_id, owner, outcome),
    )
    return outcome


_trace_failures: dict[str, deque[float]] = {}  # owner -> times of failed lookups in this process (monotonic)
_trace_lock = threading.Lock()
_TRACE_OWNERS_KEPT = 1000


def _trace_limited(owner: str, *, record: bool) -> bool:
    """Whether `owner` has hit the failed-lookup limit; with `record`, count one more failure. Lookups keep their own
    in-memory count so they never write `redemption_outcomes` (the S33 panels and OB-6 count real redemption
    attempts) and never lock an owner out of /verify. The count is per process: admin-only, so this is a brake on
    enumeration, not a shared quota."""
    settings = get_settings()
    now = time.monotonic()
    with _trace_lock:
        if len(_trace_failures) > _TRACE_OWNERS_KEPT:
            stale = [k for k, v in _trace_failures.items() if not v or now - v[-1] > settings.rate_limit_window_seconds]
            for key in stale:
                del _trace_failures[key]
        times = _trace_failures.setdefault(owner, deque())
        while times and now - times[0] > settings.rate_limit_window_seconds:
            times.popleft()
        limited = len(times) >= settings.rate_limit_max_failures
        if record and not limited:
            times.append(now)
        return limited


def find_issuance(owner: str, value: str) -> str:
    """The issuance reference of the owner's stored token for `value` (F5b trace). The value is only hashed. An unknown
    token and a seed token (it has no issuance) raise the same NotFound; only the unknown one counts as a failed
    guess. Failed lookups are limited per owner (AZ-6) so the lookup cannot be used to guess a code, but on their own
    counter, not in the redemption outcomes."""
    check_owner(owner)
    presented = hash_token(owner, value)
    if _trace_limited(owner, record=False):
        raise RateLimited("too many failed attempts for this owner; try again later")
    with db.connection() as conn:
        rows = conn.execute("SELECT issuance_id, token_hash FROM tokens WHERE owner = %s", (owner,)).fetchall()
    matches = [row[0] for row in rows if hashes_equal(bytes(row[1]), presented)]
    found = [issuance for issuance in matches if issuance is not None]
    if not found:
        if not matches:
            _trace_limited(owner, record=True)
        raise NotFound("no such token")
    return str(found[0])


def redeem(owner: str, value: str) -> str:
    """Redeem the owner's token for `value`; record exactly one outcome (never the value) in the same transaction."""
    check_owner(owner)
    presented = hash_token(owner, value)
    settings = get_settings()
    with db.connection() as conn:
        _lock_owner(conn, owner, settings.rate_limit_lock_timeout_ms)
        failures = conn.execute(
            "SELECT count(*) FROM redemption_outcomes WHERE owner = %s AND outcome = ANY(%s)"
            " AND attempted_at > clock_timestamp() - make_interval(secs => %s)",
            (owner, list(FAILED_OUTCOMES), settings.rate_limit_window_seconds),
        ).fetchone()[0]
        if failures >= settings.rate_limit_max_failures:
            return _record(conn, None, owner, "rate_limited")
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
        return _record(conn, token_id, owner, outcome)
