"""Entropy source: the swappable upstream client (CH-1). Owns nothing.

S01 STUB: no network. The real Qrypt client and the mock service: S08 and S09.
"""

from datetime import UTC, datetime

from qrypt_entropy.errors import InvalidArgument
from qrypt_entropy.models import Fetched
from qrypt_entropy.stubs import stub


@stub("S08")
async def fetch(size_kib: int) -> Fetched:
    if not 1 <= size_kib <= 512:
        raise InvalidArgument("size must be 1 to 512 KiB")
    return Fetched("stub", b"\x00" * (size_kib * 1024), datetime.now(UTC).isoformat())


@stub("S09")
def request_stats() -> dict:
    return {"requestsLast10s": 0}
