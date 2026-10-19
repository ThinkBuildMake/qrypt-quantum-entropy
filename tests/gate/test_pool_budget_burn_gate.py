"""H03 gate cases: a persistent upstream fault must not burn the budget while receiving nothing (GC-4, RL-4).

Meter.reserve and Entropy source.fetch are stubbed and count; the last case runs the real Meter.
"""

import asyncio
import time
import uuid

import pytest

from qrypt_entropy.errors import AllotmentUsed, BudgetExhausted, Misconfigured, PoolStarved, Unavailable
from qrypt_entropy.models import Batch, Fetched
from qrypt_entropy.service import meter
from qrypt_entropy.service import pool as pool_module
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.upstream import source

KIB = 1024


class Counting:
    def __init__(self, monkeypatch):
        self.reserved_kib = 0
        self.reserves = 0
        self.fetch_error: Exception | None = None
        self.reserve_error: Exception | None = None
        monkeypatch.setattr(meter, "reserve", self.reserve)
        monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: None)
        monkeypatch.setattr(source, "fetch", self.fetch)

    def reserve(self, wanted_kib, purpose, src):
        if self.reserve_error:
            raise self.reserve_error
        self.reserves += 1
        self.reserved_kib += wanted_kib
        return Batch(str(uuid.uuid4()), wanted_kib)

    async def fetch(self, size_kib):
        if self.fetch_error:
            raise self.fetch_error
        return Fetched("stub", bytes(size_kib * KIB))


@pytest.fixture
def counting(monkeypatch):
    return Counting(monkeypatch)


@pytest.mark.parametrize("error", [Misconfigured("401"), AllotmentUsed("403")])
async def test_sticky_fatal_fault_reserves_one_batch(counting, error):
    counting.fetch_error = error
    p = Pool()
    start = time.monotonic()
    for _ in range(50):
        with pytest.raises(PoolStarved):
            await p.take(10)
        await asyncio.sleep(0.005)
    assert time.monotonic() - start < 2.2 * 2
    assert counting.reserved_kib <= 2 * pool_module.REFILL_KIB
    await p.stop()


async def test_fatal_fault_is_retried_after_the_interval_and_recovers(counting, monkeypatch):
    monkeypatch.setattr(pool_module, "FATAL_RETRY_S", 0.3)
    counting.fetch_error = Misconfigured("401")
    p = Pool()
    with pytest.raises(PoolStarved):
        await p.take(10)
    counting.fetch_error = None  # the operator fixed the token
    await asyncio.sleep(0.35)
    assert len((await p.take(10)).bytes) == 10  # the first take after the interval tries again and is served
    await p.stop()


async def test_sustained_transient_fault_bounds_reserves(counting, monkeypatch):
    monkeypatch.setattr(pool_module, "BACKOFF_BASE_S", 0.02)
    monkeypatch.setattr(pool_module, "HARD_BACKOFF_S", 0.5)
    counting.fetch_error = Unavailable("503")
    p = Pool()
    p.add("A", bytes(10))
    await p.take(1)  # below the low mark: starts the refill loop with nobody waiting
    await asyncio.sleep(1.3)  # nobody is waiting: after the streak there are no more reserves
    assert counting.reserves == pool_module.FAILURES_BEFORE_HARD_BACKOFF
    for _ in range(3):  # takes that wait may each trigger one attempt, but not more than one per interval
        with pytest.raises(PoolStarved):
            await p.take(100)
    assert counting.reserves <= pool_module.FAILURES_BEFORE_HARD_BACKOFF + 3
    await p.stop()


async def test_transient_fault_recovers_when_it_clears(counting, monkeypatch):
    monkeypatch.setattr(pool_module, "BACKOFF_BASE_S", 0.02)
    monkeypatch.setattr(pool_module, "HARD_BACKOFF_S", 0.3)
    counting.fetch_error = Unavailable("503")
    p = Pool()
    for _ in range(3):
        with pytest.raises(PoolStarved):
            await p.take(10)
    counting.fetch_error = None
    await asyncio.sleep(0.35)
    assert len((await p.take(10)).bytes) == 10
    assert p.status().upstream_status == "ok"
    await p.stop()


async def test_a_stale_budget_error_is_retried_at_once(counting, monkeypatch):
    """Item 8: after an admin raises the cap, the next take is served instead of failing for up to 10 s."""
    counting.reserve_error = BudgetExhausted("spent")
    p = Pool()
    with pytest.raises(PoolStarved) as err:
        await p.take(10)
    assert err.value.reason == "budget_exhausted"
    await asyncio.sleep(0.1)  # the loop is now in its backoff sleep
    counting.reserve_error = None  # the cap was raised
    start = time.monotonic()
    assert len((await p.take(10)).bytes) == 10
    assert time.monotonic() - start < 1.0
    await p.stop()


async def test_sticky_401_with_the_real_meter(db, monkeypatch):
    async def refuse(size_kib):
        raise Misconfigured("401")

    monkeypatch.setattr(source, "fetch", refuse)
    p = Pool()
    for _ in range(50):
        with pytest.raises(PoolStarved):
            await p.take(10)
        await asyncio.sleep(0.01)
    await p.stop()
    await asyncio.to_thread(pool_module.wait_for_orphans)
    used = meter.get_usage()["budget"]["usedKib"]
    assert used <= 2 * pool_module.REFILL_KIB
