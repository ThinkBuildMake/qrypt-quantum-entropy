"""S10: the in-memory pool, tested against stubbed Meter.reserve and Entropy source.fetch."""

import asyncio
import json
import uuid

import pytest

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import PoolStarved
from qrypt_entropy.logs import configure_logging
from qrypt_entropy.models import Batch, Fetched
from qrypt_entropy.service import meter
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.upstream import source

KIB = 1024


class FakeUpstream:
    """Stubs Meter.reserve and source.fetch: every batch is a run of one distinct byte value."""

    def __init__(self, monkeypatch, marker: bytes | None = None, delay: float = 0.001, pool: Pool | None = None):
        self.pool, self.levels_at_reserve = pool, []
        self.asked_kib: list[int] = []
        self.open = self.max_open = 0
        self.marker, self.delay, self.count = marker, delay, 0
        monkeypatch.setattr(meter, "reserve", self.reserve)
        monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: None)
        monkeypatch.setattr(source, "fetch", self.fetch)

    def reserve(self, wanted_kib, purpose, src):
        self.asked_kib.append(wanted_kib)
        if self.pool is not None:
            self.levels_at_reserve.append(self.pool.level)
        return Batch(str(uuid.uuid4()), wanted_kib)

    async def fetch(self, size_kib):
        self.open += 1
        self.max_open = max(self.max_open, self.open)
        try:
            await asyncio.sleep(self.delay)
            self.count += 1
            data = self.marker * (size_kib * KIB // len(self.marker) + 1) if self.marker else bytes([self.count % 256])
            return Fetched("stub", (data * size_kib * KIB)[: size_kib * KIB])
        finally:
            self.open -= 1


async def take_retrying(pool: Pool, n: int):
    while True:
        try:
            return await pool.take(n)
        except PoolStarved:
            await asyncio.sleep(0.001)


async def hammer(pool: Pool, callers=100, calls=10_000, n=16, sample_every=None):
    results, levels = [], []

    async def caller(count):
        for _ in range(count):
            results.append(await take_retrying(pool, n))

    async def sampler():
        while True:
            levels.append(pool.status().level_bytes)
            await asyncio.sleep(0.01)

    watcher = asyncio.create_task(sampler())
    await asyncio.gather(*(caller(calls // callers) for _ in range(callers)))
    watcher.cancel()
    await pool.stop()
    return results, levels


async def test_pool_no_overlap(monkeypatch):
    FakeUpstream(monkeypatch)
    results, _ = await hammer(Pool())
    ranges = [(s.batch_id, s.byte_offset, s.byte_offset + s.byte_count) for r in results for s in r.slices]
    assert len(results) == 10_000
    ranges.sort()
    for (b1, _, end), (b2, start, _) in zip(ranges, ranges[1:], strict=False):
        assert b1 != b2 or end <= start


async def test_pool_slices_across_a_batch_boundary(monkeypatch):
    FakeUpstream(monkeypatch)
    pool = Pool()
    pool.add("A", b"abc")
    pool.add("B", b"defgh")
    taken = await pool.take(6)
    assert taken.bytes == b"abcdef"
    assert [(s.batch_id, s.byte_offset, s.byte_count) for s in taken.slices] == [("A", 0, 3), ("B", 0, 3)]
    assert (await pool.take(2)).bytes == b"gh"
    await pool.stop()


async def test_pool_refill_below_low(monkeypatch):
    pool = Pool(32 * KIB, 128 * KIB)
    up = FakeUpstream(monkeypatch, pool=pool)
    pool.add("A", bytes(40 * KIB))
    await pool.take(7 * KIB)  # 33 KiB: not below the low mark
    await asyncio.sleep(0.01)
    assert up.asked_kib == []
    await pool.take(3 * KIB)
    await asyncio.sleep(0.05)
    assert up.asked_kib == [96] and up.levels_at_reserve == [30 * KIB] and pool.level == 126 * KIB
    await pool.stop()


async def test_pool_high_mark(monkeypatch):
    FakeUpstream(monkeypatch, delay=0.02)
    _, levels = await hammer(Pool(32 * KIB, 128 * KIB))
    assert len(levels) > 5 and max(levels) <= 128 * KIB


async def test_pool_single_refill(monkeypatch):
    up = FakeUpstream(monkeypatch)
    await hammer(Pool())
    assert up.count > 1 and up.max_open == 1


async def test_pool_marks_config(monkeypatch):
    monkeypatch.setenv("POOL_LOW_MARK_BYTES", str(8 * KIB))
    monkeypatch.setenv("POOL_HIGH_MARK_BYTES", str(16 * KIB))
    get_settings.cache_clear()
    pool = Pool()
    up = FakeUpstream(monkeypatch, pool=pool)
    _, levels = await hammer(pool, calls=2_000)
    get_settings.cache_clear()
    assert (pool.low, pool.high) == (8 * KIB, 16 * KIB)
    assert up.count > 1 and max(levels) <= 16 * KIB and max(up.asked_kib) <= 16
    # the first refill was asked for only when the pool was empty (below 8 KiB)
    assert up.asked_kib[0] == 16
    assert up.levels_at_reserve and all(level < 8 * KIB for level in up.levels_at_reserve)


async def test_pool_memory_only(monkeypatch, tmp_path, capsys):
    marker = b"QRYPT-MARKER-\xde\xad\xbe\xef"
    log_file = tmp_path / "server.log"
    configure_logging("DEBUG", str(log_file))
    messages = []
    up = FakeUpstream(monkeypatch, marker=marker)
    pool = Pool(8 * KIB, 16 * KIB)
    pool.add("A", marker * 4)
    await pool.take(len(marker))
    await asyncio.sleep(0.05)
    try:
        await pool.take(10**6)
    except PoolStarved as exc:
        messages.append(str(exc) + repr(exc))

    async def broken_fetch(size_kib):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(source, "fetch", broken_fetch)
    await pool.stop()
    failing = Pool(8 * KIB, 16 * KIB)
    failing.add("B", marker)
    await failing.take(1)  # below low: a refill starts, fails and is logged
    await asyncio.sleep(0.02)
    restarted = Pool()
    await failing.stop()
    out = capsys.readouterr()
    haystacks = [log_file.read_bytes(), (out.out + out.err).encode(), "".join(messages).encode()]
    haystacks += [p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()]
    assert up.count >= 1 and all(marker not in h and marker.hex().encode() not in h for h in haystacks)
    assert b"pool_refill_failed" in log_file.read_bytes() and json.loads(log_file.read_text().splitlines()[0])
    assert restarted.level == 0 and pool.level == 0
    configure_logging()


async def test_pool_stop(monkeypatch):
    pool = Pool()
    pool.add("A", bytes(64 * KIB))
    await pool.stop()
    assert pool.level == 0 and pool.status().level_bytes == 0
    with pytest.raises(PoolStarved):
        await pool.take(1)


async def test_take_during_stop_starts_no_refill(monkeypatch):
    pool = Pool(32 * KIB, 128 * KIB)
    up = FakeUpstream(monkeypatch, pool=pool, delay=0.05)
    pool.add("A", bytes(20 * KIB))
    await pool.take(1)  # refill in flight
    await asyncio.sleep(0.01)
    reserved_before = len(up.asked_kib)
    stopping = asyncio.create_task(pool.stop())
    await asyncio.sleep(0)  # stop has begun and is waiting for the cancelled refill
    with pytest.raises(PoolStarved):
        await pool.take(10**6)
    await pool.take(1)  # leaves the level below low while stopping
    await stopping
    await asyncio.sleep(0.1)
    assert pool.level == 0 and len(up.asked_kib) == reserved_before


async def test_wrong_size_fetch_is_discarded(monkeypatch):
    pool = Pool(32 * KIB, 128 * KIB)
    FakeUpstream(monkeypatch)
    marks = []

    async def short_fetch(size_kib):
        return Fetched("stub", bytes(size_kib * KIB - 1))

    monkeypatch.setattr(source, "fetch", short_fetch)
    monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: marks.append(outcome))
    pool.add("A", bytes(10))
    await pool.take(1)
    await asyncio.sleep(0.02)
    assert pool.level == 9 and marks == ["failed"]


async def test_startup_check_bytes_are_served_after_every_refill_batch():  # S20 AC4, DEVIATIONS 17
    pool = Pool(low_mark_bytes=0, high_mark_bytes=0)
    pool.add("check", b"aaaa", last=True)
    pool.add("refill-1", b"bbbb")
    pool.add("refill-2", b"cccc")
    taken = await pool.take(10)
    assert taken.bytes == b"bbbbccccaa"
    assert [s.batch_id for s in taken.slices] == ["refill-1", "refill-2", "check"]
    pool.add("refill-3", b"dd")  # a late refill still goes ahead of the check's remaining bytes
    assert (await pool.take(4)).bytes == b"ddaa"


async def test_a_slow_meter_does_not_stall_the_event_loop(monkeypatch):  # S20: reserve/mark_batch run in a thread
    import time

    def slow_reserve(kib, purpose, src):
        time.sleep(0.3)
        return Batch(str(uuid.uuid4()), kib)

    FakeUpstream(monkeypatch)
    monkeypatch.setattr(meter, "reserve", slow_reserve)
    monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: time.sleep(0.3))
    pool = Pool(low_mark_bytes=2 * KIB, high_mark_bytes=4 * KIB)
    gaps, stop = [], asyncio.Event()

    async def ticker():
        last = asyncio.get_running_loop().time()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = asyncio.get_running_loop().time()
            gaps.append(now - last)
            last = now

    watcher = asyncio.create_task(ticker())
    await pool.start()  # a startup check: one slow reserve and one slow mark
    stop.set()
    await watcher
    await pool.stop()
    assert max(gaps) < 0.15, max(gaps)


async def test_start_warns_when_marks_are_closer_than_a_refill(monkeypatch, tmp_path):
    log_file = tmp_path / "pool.log"
    configure_logging("DEBUG", str(log_file))
    for low, high, expect in ((32 * KIB, 128 * KIB, False), (64 * KIB, 128 * KIB, True)):
        pool = Pool(low, high)
        FakeUpstream(monkeypatch, pool=pool)
        await pool.start()
        await pool.stop()
        lines = [json.loads(x) for x in log_file.read_text().splitlines()]
        assert any(x["event"] == "pool_marks_too_close" for x in lines) is expect
        log_file.write_text("")
    configure_logging()
