"""S11 gate cases: a call the pool cannot cover fails whole, within 2 s, with the right reason.

Meter.reserve and Entropy source.fetch are stubbed (docs/stories/S11-pool-fail.md AC1-AC8).
"""

import asyncio
import json
import time
import uuid
from datetime import datetime

import pytest

from qrypt_entropy.errors import (
    AllotmentUsed,
    BudgetExhausted,
    Misconfigured,
    PoolStarved,
    RateLimited,
    Unavailable,
)
from qrypt_entropy.logs import configure_logging
from qrypt_entropy.models import Batch, Fetched
from qrypt_entropy.service import meter
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.upstream import source

KIB = 1024


class Stubs:
    """Stubs Meter.reserve and source.fetch. `fetch_error`/`reserve_error` make them raise; `delay` slows a fetch."""

    def __init__(self, monkeypatch):
        self.fetch_error: Exception | None = None
        self.reserve_error: Exception | None = None
        self.delay = 0.0
        self.fetches = 0
        monkeypatch.setattr(meter, "reserve", self.reserve)
        monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: None)
        monkeypatch.setattr(source, "fetch", self.fetch)

    def reserve(self, wanted_kib, purpose, src):
        if self.reserve_error:
            raise self.reserve_error
        return Batch(str(uuid.uuid4()), wanted_kib)

    async def fetch(self, size_kib):
        self.fetches += 1
        await asyncio.sleep(self.delay)
        if self.fetch_error:
            raise self.fetch_error
        return Fetched("stub", bytes(size_kib * KIB))


@pytest.fixture
def stubs(monkeypatch):
    return Stubs(monkeypatch)


async def test_pool_waits_for_refill(stubs):
    pool = Pool()
    pool.add("A", bytes(100))
    stubs.delay = 1.0
    start = time.monotonic()
    taken = await pool.take(200)
    assert len(taken.bytes) == 200 and 0.9 <= time.monotonic() - start <= 1.3
    await pool.stop()


async def test_pool_gives_up_2s(stubs):
    pool = Pool()
    pool.add("A", bytes(100))
    stubs.delay = 3.0
    start = time.monotonic()
    with pytest.raises(PoolStarved) as err:
        await pool.take(200)
    assert err.value.code == "temporarily_unavailable"
    assert 2.0 <= time.monotonic() - start <= 2.2
    await pool.stop()


async def test_pool_fails_whole(stubs):
    pool = Pool()
    pool.add("A", bytes(1000))
    stubs.fetch_error = Unavailable("down")
    with pytest.raises(PoolStarved):
        await pool.take(1001)
    assert pool.level == 1000
    await pool.stop()


@pytest.mark.parametrize(
    ("where", "error", "reason"),
    [
        ("fetch", RateLimited("429"), "temporarily_unavailable"),
        ("fetch", Unavailable("5xx"), "temporarily_unavailable"),
        ("fetch", Misconfigured("401"), "upstream_misconfigured"),
        ("fetch", AllotmentUsed("403"), "budget_exhausted"),
        ("reserve", BudgetExhausted("spent"), "budget_exhausted"),
    ],
)
async def test_pool_reason_map(stubs, where, error, reason):
    setattr(stubs, f"{where}_error", error)
    pool = Pool()
    with pytest.raises(PoolStarved) as err:
        await pool.take(10)
    assert err.value.code == reason == err.value.reason
    await pool.stop()


async def test_pool_serves_during_429(stubs):
    stubs.fetch_error = RateLimited("429")
    pool = Pool()
    pool.add("A", bytes(10 * KIB))
    await pool.take(1)  # below the low mark: starts a refill that now fails with 429
    await asyncio.sleep(0.05)
    assert stubs.fetches >= 1 and isinstance(pool._error, RateLimited)
    results = []
    for _ in range(100):
        results.append(await pool.take(16))
        await asyncio.sleep(0)
    assert all(len(r.bytes) == 16 for r in results) and pool.level == 10 * KIB - 1 - 1600
    await pool.stop()


async def test_pool_ob8_events(stubs, tmp_path):
    log_file = tmp_path / "server.log"
    configure_logging("INFO", str(log_file))
    stubs.fetch_error = RateLimited("429")
    pool = Pool()
    with pytest.raises(PoolStarved):
        await pool.take(10)
    await pool.stop()
    events = {e["event"]: e for e in map(json.loads, log_file.read_text().splitlines()) if "event" in e}
    configure_logging()
    first, returned = events["pool_cannot_cover"], events["pool_error_returned"]
    assert returned["reason"] == "temporarily_unavailable"
    gap = datetime.fromisoformat(returned["timestamp"]) - datetime.fromisoformat(first["timestamp"])
    assert 0 <= gap.total_seconds() <= 2.0 + 0.2  # RL-1: within 2 s (plus scheduling slack)
    assert 1.9 <= returned["waited_s"] <= 2.2


async def test_pool_serves_after_budget(stubs):
    stubs.reserve_error = BudgetExhausted("spent")
    pool = Pool()
    pool.add("A", bytes(500))
    await pool.take(1)  # starts a refill, which the meter refuses
    await asyncio.sleep(0.05)
    assert isinstance(pool._error, BudgetExhausted)
    assert len((await pool.take(100)).bytes) == 100
    await pool.stop()


async def test_pool_refills_for_a_waiter_above_low(stubs):
    pool = Pool(1 * KIB, 128 * KIB)
    pool.add("A", bytes(2 * KIB))  # at or above the low mark: only the waiter needs a refill
    start = time.monotonic()
    assert len((await pool.take(3 * KIB)).bytes) == 3 * KIB
    assert time.monotonic() - start < 1.0
    await pool.stop()


async def test_pool_exhausted_too_big(stubs):
    stubs.reserve_error = BudgetExhausted("spent")
    pool = Pool()
    pool.add("A", bytes(400))
    with pytest.raises(PoolStarved) as err:
        await pool.take(1000)
    assert err.value.code == "budget_exhausted" and pool.level == 400
    await pool.stop()


async def test_failed_refill_backs_off(stubs):
    stubs.fetch_error = Unavailable("down")
    pool = Pool()
    pool.add("A", bytes(10))
    await pool.take(1)
    await asyncio.sleep(0.2)  # the shortest pause is 0.25 s
    assert stubs.fetches == 1  # one attempt, then a wait: no retry storm
    await pool.stop()
