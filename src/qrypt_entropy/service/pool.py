"""Pool: the in-memory entropy pool. Owns nothing in the database.

`Pool` (S10) is the real pool: it hands each byte out once and refills itself in the background. S11 adds the wait
for a refill and the failure paths; S12 adds start + status. S20: the MCP server process owns one `Pool` (`get_pool`),
starts it at server start and stops it at shutdown; the module-level `take` is the issuance code's way in.
Pooled bytes live in this process's memory only (CP-2): never written to a file, a log or an error message (OB-3).
"""

import asyncio
import random
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import BudgetExhausted, Misconfigured, PoolStarved, TemporarilyUnavailable, Unavailable
from qrypt_entropy.logs import get_logger
from qrypt_entropy.models import Batch, BatchSlice, PoolStatus, Taken
from qrypt_entropy.service import meter
from qrypt_entropy.upstream import source

log = get_logger("qrypt_entropy.pool")
KIB = 1024
REFILL_KIB = 64  # PF-3
WAIT_S = 1.8  # RL-1, RL-3: how long a take waits for a refill; the error is out within 2 s
_RESERVATIONS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reserve")  # blocking Meter calls, off the loop
BACKOFF_BASE_S, BACKOFF_MAX_S = 0.5, 10.0  # between failed refills, doubled each time, with jitter (DE-3)


def reason_for(exc: Exception) -> str:
    """The caller-facing reason for an Entropy source or Meter error (ARCHITECTURE: Pool)."""
    if isinstance(exc, BudgetExhausted):  # includes AllotmentUsed
        return "budget_exhausted"
    if isinstance(exc, Misconfigured):
        return "upstream_misconfigured"
    return "temporarily_unavailable"


class Pool:
    """Not thread-safe by design: every method runs on one event loop and `take` never awaits."""

    def __init__(self, low_mark_bytes: int | None = None, high_mark_bytes: int | None = None):
        settings = get_settings()
        self.low = settings.pool_low_mark_bytes if low_mark_bytes is None else low_mark_bytes
        self.high = settings.pool_high_mark_bytes if high_mark_bytes is None else high_mark_bytes
        self._chunks: deque[list] = deque()  # [batch_id, bytes, next offset]
        self._check_chunk: list | None = None  # the startup check's chunk while it is still in `_chunks`
        self._level = 0
        self._refill: asyncio.Task | None = None
        self._stopped = False
        self._waiting: list[int] = []  # sizes of the takes now waiting for a refill
        self._error: Exception | None = None  # why the latest refill failed; cleared by the next success
        self._last_success_at: str | None = None
        self._changed = asyncio.Event()  # set (and replaced) whenever bytes arrive, a refill fails or the pool stops

    @property
    def level(self) -> int:
        return self._level

    def add(self, batch_id: str, data: bytes, *, last: bool = False) -> None:
        """Join the pool. `last` marks the startup check's bytes, served only after every refill batch, so what a
        client sees after the first refill names a 64 KiB batch, not the 1 KiB check (S20 AC4)."""
        chunk = [batch_id, data, 0]
        place = next((i for i, c in enumerate(self._chunks) if c is self._check_chunk), None)
        if place is None or last:
            self._chunks.append(chunk)
        else:
            self._chunks.insert(place, chunk)
        if last:
            self._check_chunk = chunk
        self._level += len(data)

    async def take(self, n: int) -> Taken:
        """Remove n bytes in one step, or none (VR-8). Waits up to WAIT_S for a refill to cover them (RL-3)."""
        if n < 1:
            raise TemporarilyUnavailable("cannot take fewer than one byte")
        if n > self._level:
            await self._wait_to_cover(n)
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

    async def _wait_to_cover(self, n: int) -> None:
        """Return once the pool holds n bytes, or raise PoolStarved. Removes nothing."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        log.warning("pool_cannot_cover", wanted_bytes=n, level_bytes=self._level)
        self._waiting.append(n)
        try:
            async with asyncio.timeout(WAIT_S):
                while n > self._level:
                    if self._stopped:
                        raise PoolStarved("the pool is stopped")
                    self._ensure_refill()
                    if self._error is not None and reason_for(self._error) != "temporarily_unavailable":
                        raise PoolStarved("the pool cannot be refilled", reason_for(self._error))
                    await self._changed.wait()
        except TimeoutError:
            exc = PoolStarved(
                "the pool could not be refilled in time",
                reason_for(self._error) if self._error else "temporarily_unavailable",
            )
        except PoolStarved as err:
            exc = err
        else:
            return
        finally:
            self._waiting.remove(n)
        log.warning("pool_error_returned", reason=exc.reason, wanted_bytes=n, waited_s=round(loop.time() - started, 3))
        raise exc

    def _notify(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def status(self) -> PoolStatus:
        """ok, or after a failed fetch: misconfigured (401), failing (a call cannot be covered) or degraded."""
        if self._error is None:
            state = "ok"
        elif isinstance(self._error, Misconfigured):
            state = "misconfigured"
        elif self._level == 0 or self._waiting:
            state = "failing"
        else:
            state = "degraded"
        return PoolStatus(self._level, self.low, self.high, state, self._last_success_at)

    async def start(self) -> None:
        """Run the 1 KiB startup check (DE-5), then begin the refill loop. A failed check is logged, never raised."""
        self._stopped, self._error, self._last_success_at = False, None, None
        batch = None
        try:
            await asyncio.to_thread(meter.settle_stale)  # batches a killed process left 'requested'
        except Exception as exc:
            log.warning("batch_settle_stale_failed", error=type(exc).__name__)
        try:
            batch = await self._reserve(1, "startup_check")
            fetched = await source.fetch(batch.kib)
            if len(fetched.bytes) != batch.kib * KIB:
                raise Unavailable("the entropy source sent the wrong amount")
            if self._stopped:  # stop() ran during the check
                await self._mark_failed(batch.batch_id)
                log.warning("pool_startup_check_discarded", reason="stopped")
                return
            await self._succeeded(batch.batch_id, fetched.bytes, last=True)
        except BaseException as exc:
            if batch is not None:
                await self._mark_failed(batch.batch_id)
            if not isinstance(exc, Exception):
                raise
            self._error = exc
            log.error("pool_startup_check_failed", cause=str(exc), reason=reason_for(exc))
        if not isinstance(self._error, Misconfigured):  # a bad token is not retried until a take asks
            self._ensure_refill()

    async def _reserve(self, kib: int, purpose: str) -> Batch:
        """Meter.reserve is a blocking database call: run it off the event loop so waiting takes are not stalled.
        If the caller is cancelled while it runs, the reservation still completes, and the worker thread settles
        that batch as failed (nobody will fetch for it)."""
        job = _RESERVATIONS.submit(meter.reserve, kib, purpose, source.label())
        try:
            return await asyncio.shield(asyncio.wrap_future(job))
        except asyncio.CancelledError:
            job.add_done_callback(_settle_orphan)
            raise

    async def _mark_failed(self, batch_id: str) -> None:
        try:
            await asyncio.to_thread(meter.mark_batch, batch_id, "failed")
        except Exception as exc:  # the original failure is the one to report
            log.error("batch_mark_failed", error=type(exc).__name__)

    async def _succeeded(self, batch_id: str, data: bytes, *, last: bool = False) -> None:
        """Record the batch as received, then let its bytes in (so an issuance never names a pending batch)."""
        await asyncio.to_thread(meter.mark_batch, batch_id, "received")
        if self._stopped:
            return
        self._error, self._last_success_at = None, datetime.now(UTC).isoformat()
        self.add(batch_id, data, last=last)
        self._notify()

    async def stop(self) -> None:
        """End the refill loop and discard what the pool holds."""
        self._stopped = True  # before awaiting: a take during the cancel must not start a new refill
        self._notify()
        task, self._refill = self._refill, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._chunks.clear()
        self._check_chunk = None
        self._level = 0

    def _ensure_refill(self) -> None:
        if not self._stopped and (self._refill is None or self._refill.done()):
            self._refill = asyncio.get_running_loop().create_task(self._refill_loop())

    async def _refill_loop(self) -> None:
        """At most one runs at a time. A failure is recorded for waiting takes, then retried after a growing pause."""
        failures = 0
        while self._level < max([self.low, *self._waiting]) and not self._stopped:
            headroom_kib = (self.high - self._level) // KIB
            if headroom_kib < 1:
                return
            batch = None
            try:
                batch = await self._reserve(min(REFILL_KIB, headroom_kib), "refill")
                fetched = await source.fetch(batch.kib)
                if len(fetched.bytes) != batch.kib * KIB:
                    raise Unavailable("the entropy source sent the wrong amount")
                if self._stopped:
                    await self._mark_failed(batch.batch_id)
                    log.warning("pool_refill_discarded", reason="stopped")
                    return
                await self._succeeded(batch.batch_id, fetched.bytes)
            except BaseException as exc:
                if batch is not None:
                    await self._mark_failed(batch.batch_id)
                if not isinstance(exc, Exception):
                    raise
                failures += 1
                self._error = exc
                if isinstance(exc, Misconfigured):  # retrying a bad token only burns budget: the next take may try once
                    log.error("pool_refill_failed", error="Misconfigured", cause=str(exc))
                    self._notify()
                    return
                delay = min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (failures - 1)) * random.uniform(0.5, 1)  # noqa: S311
                log.warning(
                    "pool_refill_failed", error=type(exc).__name__, attempt=failures, retry_in_s=round(delay, 2)
                )
                self._notify()
                await asyncio.sleep(delay)
                continue
            failures = 0


def _settle_orphan(job: Future) -> None:
    """Called when a cancelled refill's reservation finishes. Never blocks: the database write goes to a worker
    (the callback may run on the event loop if the job was already done)."""
    if job.cancelled() or job.exception() is not None:
        return
    _RESERVATIONS.submit(_mark_orphan_failed, job.result().batch_id)


def _mark_orphan_failed(batch_id: str) -> None:
    try:
        meter.mark_batch(batch_id, "failed")
    except Exception as exc:
        log.error("batch_mark_failed", error=type(exc).__name__)


_pool: Pool | None = None


def get_pool() -> Pool:
    """The process-wide pool, built from the settings on first use (the MCP server starts and stops it)."""
    global _pool
    if _pool is None:
        _pool = Pool()
    return _pool


def use_pool(pool: Pool | None) -> None:
    """Swap the process-wide pool; None forgets it (tests reset the singleton between cases)."""
    global _pool
    _pool = pool


async def shutdown() -> None:
    """Stop the process-wide pool, discarding its bytes, and forget it."""
    pool = _pool
    use_pool(None)
    if pool is not None:
        await pool.stop()


async def take(n: int) -> Taken:
    return await get_pool().take(n)


def status() -> PoolStatus:
    return get_pool().status()
