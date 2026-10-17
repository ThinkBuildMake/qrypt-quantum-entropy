"""Pool: the in-memory entropy pool. Owns nothing in the database.

`Pool` (S10) is the real pool: it hands each byte out once and refills itself in the background. The module-level
`take`/`status` below stay S01 stubs (fixed pattern, not entropy, so a stub value is recognisable) until S20 wires a
running `Pool` into the flows. S11 adds the failure paths and the wait for a refill, S12 start and the startup check.
Pooled bytes live in this process's memory only (CP-2): never written to a file, a log or an error message (OB-3).
"""

import asyncio
from collections import deque

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import TemporarilyUnavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import BatchSlice, PoolStatus, Taken
from qrypt_entropy.service import meter
from qrypt_entropy.stubs import stub
from qrypt_entropy.upstream import source

log = get_logger("qrypt_entropy.pool")
KIB = 1024
REFILL_KIB = 64  # PF-3


class Pool:
    """Not thread-safe by design: every method runs on one event loop and `take` never awaits."""

    def __init__(self, low_mark_bytes: int | None = None, high_mark_bytes: int | None = None):
        settings = get_settings()
        self.low = settings.pool_low_mark_bytes if low_mark_bytes is None else low_mark_bytes
        self.high = settings.pool_high_mark_bytes if high_mark_bytes is None else high_mark_bytes
        self._chunks: deque[list] = deque()  # [batch_id, bytes, next offset]
        self._level = 0
        self._refill: asyncio.Task | None = None
        self._stopped = False

    @property
    def level(self) -> int:
        return self._level

    def add(self, batch_id: str, data: bytes) -> None:
        self._chunks.append([batch_id, data, 0])
        self._level += len(data)

    async def take(self, n: int) -> Taken:
        """Remove n bytes in one step, or none (VR-8). S11 replaces the immediate failure with a wait."""
        if n < 1:
            raise TemporarilyUnavailable("cannot take fewer than one byte")
        if n > self._level:
            self._ensure_refill()
            raise TemporarilyUnavailable("the pool cannot cover this call yet")
        out, slices = bytearray(), []
        while len(out) < n:
            chunk = self._chunks[0]
            batch_id, data, offset = chunk
            count = min(n - len(out), len(data) - offset)
            out += data[offset : offset + count]
            slices.append(BatchSlice(batch_id, offset, count))
            chunk[2] += count
            if chunk[2] == len(data):
                self._chunks.popleft()
        self._level -= n
        if self._level < self.low:
            self._ensure_refill()
        return Taken(bytes(out), slices)

    def status(self) -> PoolStatus:
        return PoolStatus(self._level, self.low, self.high, "ok", None)

    async def stop(self) -> None:
        """End the refill loop and discard what the pool holds."""
        self._stopped = True  # before awaiting: a take during the cancel must not start a new refill
        task, self._refill = self._refill, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._chunks.clear()
        self._level = 0

    def _ensure_refill(self) -> None:
        if not self._stopped and (self._refill is None or self._refill.done()):
            self._refill = asyncio.get_running_loop().create_task(self._refill_loop())

    async def _refill_loop(self) -> None:
        """At most one runs at a time. Ends on the first failure; the next take starts another (S11 adds backoff)."""
        while self._level < self.low:
            headroom_kib = (self.high - self._level) // KIB
            if headroom_kib < 1:
                return
            batch = None
            try:
                batch = meter.reserve(min(REFILL_KIB, headroom_kib), "refill", "qrypt")
                fetched = await source.fetch(batch.kib)
            except BaseException as exc:
                if batch is not None:
                    meter.mark_batch(batch.batch_id, "failed")
                if isinstance(exc, Exception):
                    log.warning("pool_refill_failed", error=type(exc).__name__)
                    return
                raise
            if len(fetched.bytes) != batch.kib * KIB or self._stopped:
                meter.mark_batch(batch.batch_id, "failed")
                log.warning("pool_refill_discarded", reason="stopped" if self._stopped else "wrong_size")
                return
            self.add(batch.batch_id, fetched.bytes)
            meter.mark_batch(batch.batch_id, "received")


STUB_BATCH_ID = "00000000-0000-4000-8000-0000000000b1"


@stub("S20")
async def take(n: int) -> Taken:
    if n < 1:
        raise TemporarilyUnavailable("cannot take fewer than one byte")
    return Taken(bytes(i % 256 for i in range(n)), [BatchSlice(STUB_BATCH_ID, 0, n)])


@stub("S35")
def status() -> PoolStatus:
    return PoolStatus(65536, 32768, 131072, "ok", None)


@stub("S12")
async def start() -> None:
    return None


@stub("S12")
async def stop() -> None:
    return None
