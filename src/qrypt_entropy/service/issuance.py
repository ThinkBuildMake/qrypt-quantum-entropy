"""Issuance: the tools and the audit record. Owns `issuances`, `issuance_batches`.

get_random_bytes and the audit record are real (S13), and random_int (S14). Still S01 stubs: S15 (generate_token),
S23 (getIssuance); wired to the transports by S20 and S33.
"""

import asyncio
import base64
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import psycopg

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError, InvalidArgument, TemporarilyUnavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import BatchSlice, IssuedValue, Principal, ToolMetadata
from qrypt_entropy.service import pool
from qrypt_entropy.storage.db import connection
from qrypt_entropy.stubs import stub

log = get_logger("qrypt_entropy.issuance")
SAFE_INT = 2**53 - 1
KINDS = {"api_key": "stub-api-key-0000", "otp_6": "000000", "uuid_v4": "00000000-0000-4000-8000-000000000000"}
_calls: dict[str, int] = {}
_errors: dict[str, int] = {}
_durations_ms: deque[float] = deque(maxlen=10_000)


def _metadata(n: int, batch_id: str) -> ToolMetadata:
    return ToolMetadata(
        source="stub", batch_id=batch_id, batch_ids=[batch_id], bytes_consumed=n, reference=str(uuid.uuid4())
    )


def _count(tool: str) -> None:
    _calls[tool] = _calls.get(tool, 0) + 1


def _audit(principal: Principal, tool: str, n: int, slices: list[BatchSlice]) -> ToolMetadata:
    """One transaction: the issuance row and its batch rows. Commits before the caller sees any value (VR-9, RL-6)."""
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

        async def draw(n: int) -> bytes:  # every byte drawn counts as consumed, kept or rejected (CT-1)
            taken = await pool.take(n)
            slices.extend(taken.slices)
            return taken.bytes

        value = min + await sample_int(max - min + 1, draw)
        metadata = await asyncio.to_thread(_audit, principal, tool, sum(s.byte_count for s in slices), slices)
    except AppError as err:  # a failed later draw discards the bytes already drawn and writes no row
        record_error(err.code, (time.perf_counter() - started) * 1000)
        raise
    _count(tool)
    _durations_ms.append((time.perf_counter() - started) * 1000)
    return IssuedValue(value=value, metadata=metadata)


@stub("S15")
async def generate_token(
    principal: Principal, kind: str, owner: str | None = None, expiry_seconds: int | None = None
) -> IssuedValue:
    settings = get_settings()
    if kind not in KINDS:
        raise InvalidArgument("kind must be api_key, otp_6 or uuid_v4")
    if owner is None and expiry_seconds is not None:
        raise InvalidArgument("expiry_seconds needs an owner")
    if owner is not None and len(owner) > 256:
        raise InvalidArgument("owner must be at most 256 characters")
    if expiry_seconds is not None and not 1 <= expiry_seconds <= settings.token_expiry_max_seconds:
        raise InvalidArgument("expiry_seconds out of range")
    taken = await pool.take(32)
    _count("generate_token")
    return IssuedValue(value=KINDS[kind], metadata=_metadata(32, taken.slices[0].batch_id))


@stub("S23")
def get_issuance(reference: str) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "reference": reference,
        "client": {"id": "00000000-0000-4000-8000-0000000000c1", "name": "stub-client"},
        "tool": "get_random_bytes",
        "bytesConsumed": 8,
        "createdAt": now,
        "batches": [
            {
                "batchId": pool.STUB_BATCH_ID,
                "source": "stub",
                "byteOffset": 0,
                "byteCount": 8,
                "requestedAt": now,
            }
        ],
        "token": None,
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
