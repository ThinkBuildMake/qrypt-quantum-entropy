"""Issuance: the tools and the audit record. Owns `issuances`, `issuance_batches`.

S01 STUB: values are a fixed pattern from the stub pool, no audit row. Real: S13 (get_random_bytes + audit),
S14 (random_int), S15 (generate_token), S23 (getIssuance); wired to the transports by S20 and S33.
"""

import base64
import uuid
from datetime import UTC, datetime

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import InvalidArgument
from qrypt_entropy.models import IssuedValue, Principal, ToolMetadata
from qrypt_entropy.service import pool
from qrypt_entropy.stubs import stub

SAFE_INT = 2**53 - 1
KINDS = {"api_key": "stub-api-key-0000", "otp_6": "000000", "uuid_v4": "00000000-0000-4000-8000-000000000000"}
_calls: dict[str, int] = {}


def _metadata(n: int, batch_id: str) -> ToolMetadata:
    return ToolMetadata(
        source="stub", batch_id=batch_id, batch_ids=[batch_id], bytes_consumed=n, reference=str(uuid.uuid4())
    )


def _count(tool: str) -> None:
    _calls[tool] = _calls.get(tool, 0) + 1


@stub("S13")
async def get_random_bytes(principal: Principal, length: int, encoding: str) -> IssuedValue:
    if encoding not in ("hex", "base64"):
        raise InvalidArgument("encoding must be hex or base64")
    if not 1 <= length <= get_settings().per_call_max_bytes:
        raise InvalidArgument(f"length must be 1 to {get_settings().per_call_max_bytes}")
    taken = await pool.take(length)
    value = taken.bytes.hex() if encoding == "hex" else base64.b64encode(taken.bytes).decode()
    _count("get_random_bytes")
    return IssuedValue(value=value, metadata=_metadata(length, taken.slices[0].batch_id))


@stub("S14")
async def random_int(principal: Principal, min: int, max: int) -> IssuedValue:  # noqa: A002
    if min > max or abs(min) > SAFE_INT or abs(max) > SAFE_INT:
        raise InvalidArgument("min must not exceed max, and both must be within 2^53 - 1")
    taken = await pool.take(8)
    _count("random_int")
    return IssuedValue(value=min, metadata=_metadata(8, taken.slices[0].batch_id))


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


@stub("S28")
def call_stats() -> dict:
    return {
        "callsByTool": dict(_calls),
        "errorsByCode": {},
        "toolCallDurationMs": {"p50": 0, "p95": 0, "p99": 0},
    }
