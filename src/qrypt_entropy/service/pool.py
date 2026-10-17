"""Pool: the in-memory entropy pool. Owns nothing in the database.

S01 STUB: serves a fixed pattern, not entropy, so a stub value is recognisable. Real pool: S10 (take),
S11 (failure paths), S12 (start and the startup check).
"""

from qrypt_entropy.errors import TemporarilyUnavailable
from qrypt_entropy.models import BatchSlice, PoolStatus, Taken
from qrypt_entropy.stubs import stub

STUB_BATCH_ID = "00000000-0000-4000-8000-0000000000b1"


@stub("S10")
async def take(n: int) -> Taken:
    if n < 1:
        raise TemporarilyUnavailable("cannot take fewer than one byte")
    return Taken(bytes(i % 256 for i in range(n)), [BatchSlice(STUB_BATCH_ID, 0, n)])


@stub("S10")
def status() -> PoolStatus:
    return PoolStatus(65536, 32768, 131072, "ok", None)


@stub("S12")
async def start() -> None:
    return None


@stub("S12")
async def stop() -> None:
    return None
