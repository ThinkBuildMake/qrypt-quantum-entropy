"""Issuance: the tools and the audit record. Owns `issuances`, `issuance_batches`.

get_random_bytes, random_int (S14) and generate_token (S15) are real, with the audit record, and so is getIssuance
(S23). Wired to the transports by S20 and S33.
"""

import asyncio
import base64
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

import psycopg

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, NotFound, TemporarilyUnavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import BatchSlice, IssuedValue, Principal, ToolMetadata
from qrypt_entropy.service import pool, tokens
from qrypt_entropy.storage.db import connection

log = get_logger("qrypt_entropy.issuance")
SAFE_INT = 2**53 - 1
KINDS = ("api_key", "otp_6", "uuid_v4")
OTP_LIMIT = 16_000_000  # the largest multiple of 10**6 that fits in 3 bytes (2**24 = 16,777,216)
_calls: dict[str, int] = {}
_errors: dict[str, int] = {}
_durations_ms: deque[float] = deque(maxlen=10_000)


def _count(tool: str) -> None:
    _calls[tool] = _calls.get(tool, 0) + 1


def _audit(principal: Principal, tool: str, n: int, slices: list[BatchSlice], inside=None) -> ToolMetadata:
    """One transaction: issuance row, batch rows and `inside(conn, reference)`; commits before any value returns."""
    reference = uuid.uuid4()
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, %s, %s)",
                (reference, principal.client_id, tool, n),
            )
            conn.cursor().executemany(
                "INSERT INTO issuance_batches (issuance_id, batch_id, byte_offset, byte_count) VALUES (%s, %s, %s, %s)",
                [(reference, s.batch_id, s.byte_offset, s.byte_count) for s in slices],
            )
            if inside:
                inside(conn, str(reference))
            source = conn.execute("SELECT source FROM batches WHERE id = %s", (slices[0].batch_id,)).fetchone()[0]
    except psycopg.Error as exc:
        log.error("audit_write_failed", tool=tool, error=type(exc).__name__)
        raise TemporarilyUnavailable("the call could not be recorded, so no value was issued") from exc
    batch_ids = list(dict.fromkeys(s.batch_id for s in slices))
    return ToolMetadata(
        source=source, batch_id=batch_ids[0], batch_ids=batch_ids, bytes_consumed=n, reference=str(reference)
    )


async def _audited(tool: str, principal: Principal, n: int) -> tuple[bytes, ToolMetadata]:
    taken = await pool.take(n)
    metadata = await asyncio.to_thread(_audit, principal, tool, n, taken.slices)
    return taken.bytes, metadata


def record_error(code: str, duration_ms: float) -> None:
    """Count a failed call, including one the transport rejected before it reached a tool."""
    _errors[code] = _errors.get(code, 0) + 1
    _durations_ms.append(duration_ms)


async def get_random_bytes(principal: Principal, length: int, encoding: str) -> IssuedValue:
    started = time.perf_counter()
    tool = "get_random_bytes"
    try:
        maximum = get_settings().per_call_max_bytes
        if encoding not in ("hex", "base64"):
            raise InvalidArgument("encoding must be hex or base64")
        if not 1 <= length <= maximum:
            raise InvalidArgument(f"length must be from 1 to {maximum}, the per-call maximum")
        data, metadata = await _audited(tool, principal, length)
    except AppError as err:
        record_error(err.code, (time.perf_counter() - started) * 1000)
        raise
    _count(tool)
    _durations_ms.append((time.perf_counter() - started) * 1000)
    return IssuedValue(value=data.hex() if encoding == "hex" else base64.b64encode(data).decode(), metadata=metadata)


def _drawer(slices: list[BatchSlice]) -> Callable[[int], Awaitable[bytes]]:
    """A draw function that takes from the pool and records each slice: every byte drawn, kept or rejected, is
    consumed (CT-1)."""

    async def draw(n: int) -> bytes:
        taken = await pool.take(n)
        slices.extend(taken.slices)
        return taken.bytes

    return draw


async def sample_int(span: int, draw: Callable[[int], Awaitable[bytes]]) -> int:
    """A uniform integer in [0, span) by rejection sampling (VR-2): draw just enough bytes, mask them to the bits the
    span needs, and draw again when the result falls outside it. Never a modulo. At least one byte is always drawn."""
    bits = (span - 1).bit_length()
    width = max(1, -(-bits // 8))
    mask = (1 << bits) - 1
    while True:
        candidate = int.from_bytes(await draw(width)) & mask
        if candidate < span:
            return candidate


async def random_int(principal: Principal, min: int, max: int) -> IssuedValue:  # noqa: A002
    started = time.perf_counter()
    tool = "random_int"
    try:
        if min > max or abs(min) > SAFE_INT or abs(max) > SAFE_INT:
            raise InvalidArgument("min must not exceed max, and both must be within 2^53 - 1")
        slices: list[BatchSlice] = []
        value = min + await sample_int(max - min + 1, _drawer(slices))
        metadata = await asyncio.to_thread(_audit, principal, tool, sum(s.byte_count for s in slices), slices)
    except AppError as err:  # a failed later draw discards the bytes already drawn and writes no row
        record_error(err.code, (time.perf_counter() - started) * 1000)
        raise
    _count(tool)
    _durations_ms.append((time.perf_counter() - started) * 1000)
    return IssuedValue(value=value, metadata=metadata)


async def _draw_otp(draw) -> str:
    """Six digits by rejection sampling over 3-byte draws (VR-2); every rejected draw stays counted."""
    while True:
        number = int.from_bytes(await draw(3), "big")
        if number < OTP_LIMIT:
            return f"{number % 1_000_000:06d}"


async def generate_token(
    principal: Principal, kind: str, owner: str | None = None, expiry_seconds: int | None = None
) -> IssuedValue:
    started = time.perf_counter()
    tool = "generate_token"
    settings = get_settings()
    slices: list[BatchSlice] = []
    try:
        if kind not in KINDS:
            raise InvalidArgument("kind must be api_key, otp_6 or uuid_v4")
        if owner is None and expiry_seconds is not None:
            raise InvalidArgument("expiry_seconds needs an owner")
        if owner is not None and len(owner) > tokens.MAX_OWNER_LENGTH:
            raise InvalidArgument(f"owner must be at most {tokens.MAX_OWNER_LENGTH} characters")
        if expiry_seconds is not None and not 1 <= expiry_seconds <= settings.token_expiry_max_seconds:
            raise InvalidArgument(f"expiry_seconds must be from 1 to {settings.token_expiry_max_seconds}")
        draw = _drawer(slices)
        if kind == "otp_6":
            value = await _draw_otp(draw)
        elif kind == "api_key":
            value = (await draw(32)).hex()
        else:
            raw = bytearray(await draw(16))
            raw[6] = raw[6] & 0x0F | 0x40
            raw[8] = raw[8] & 0x3F | 0x80
            value = str(uuid.UUID(bytes=bytes(raw)))
        expiry = expiry_seconds or settings.token_expiry_default_seconds
        metadata = await asyncio.to_thread(
            _audit, principal, tool, sum(s.byte_count for s in slices), slices, _store(owner, kind, value, expiry)
        )
    except AppError as err:
        record_error(err.code, (time.perf_counter() - started) * 1000)
        raise
    _count(tool)
    _durations_ms.append((time.perf_counter() - started) * 1000)
    return IssuedValue(value=value, metadata=metadata)


def _store(owner: str | None, kind: str, value: str, expiry: int):
    """The token-store step of a redeemable token's transaction, or none; any failure fails the whole issuance."""
    if owner is None:
        return None

    def inside(conn, reference: str) -> None:
        try:
            tokens.store_token(conn, reference, owner, kind, value, expiry)
        except AppError:
            raise
        except Exception as exc:
            log.error("token_store_failed", error=type(exc).__name__)
            raise TemporarilyUnavailable("the token could not be stored, so no value was issued") from exc

    return inside


def get_issuance(reference: str) -> dict:
    """Provenance of one issuance: who, which tool, how many bytes, which batches, and the token summary (FR-4).

    Reads only; never returns a value or a token hash.
    """
    with connection() as conn:
        head = conn.execute(
            "SELECT i.tool, i.bytes_consumed, i.created_at, c.id, c.name FROM issuances i "
            "JOIN credentials c ON c.id = i.client_id WHERE i.id = %s",
            (reference,),
        ).fetchone()
        if head is None:
            raise NotFound("no such issuance")
        batches = conn.execute(
            "SELECT ib.batch_id, b.source, ib.byte_offset, ib.byte_count, b.requested_at FROM issuance_batches ib "
            "JOIN batches b ON b.id = ib.batch_id WHERE ib.issuance_id = %s "
            "ORDER BY b.requested_at, ib.batch_id, ib.byte_offset",
            (reference,),
        ).fetchall()
        token = conn.execute(
            "SELECT owner, kind, expires_at, used_at, is_seed FROM tokens WHERE issuance_id = %s", (reference,)
        ).fetchone()
    tool, consumed, created_at, client_id, client_name = head
    return {
        "reference": reference,
        "client": {"id": str(client_id), "name": client_name},
        "tool": tool,
        "bytesConsumed": consumed,
        "createdAt": created_at.isoformat(),
        "batches": [
            {
                "batchId": str(batch_id),
                "source": source,
                "byteOffset": offset,
                "byteCount": count,
                "requestedAt": requested_at.isoformat(),
            }
            for batch_id, source, offset, count, requested_at in batches
        ],
        "token": None
        if token is None
        else {
            "owner": token[0],
            "kind": token[1],
            "expiresAt": token[2].isoformat(),
            "usedAt": token[3].isoformat() if token[3] else None,
            "isSeed": token[4],
        },
    }


def call_stats() -> dict:
    ordered = sorted(_durations_ms)

    def percentile(q: int) -> float:
        return round(ordered[min(len(ordered) - 1, len(ordered) * q // 100)], 3) if ordered else 0

    return {
        "callsByTool": dict(_calls),
        "errorsByCode": dict(_errors),
        "toolCallDurationMs": {"p50": percentile(50), "p95": percentile(95), "p99": percentile(99)},
    }


def reset_call_stats() -> None:
    _calls.clear()
    _errors.clear()
    _durations_ms.clear()
