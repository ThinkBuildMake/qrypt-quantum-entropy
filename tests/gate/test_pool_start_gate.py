"""S12 gate cases: Pool.start runs a 1 KiB startup check and the status values (docs/stories/S12-pool-start.md)."""

import asyncio
import uuid

import psycopg
import pytest

from qrypt_entropy.errors import Misconfigured, PoolStarved, Unavailable
from qrypt_entropy.models import Batch, Fetched
from qrypt_entropy.service import meter
from qrypt_entropy.service import pool as pool_module
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.upstream import source
from tests.mock_qrypt import MockQrypt

KIB = 1024


@pytest.fixture
async def mock(db):
    async with MockQrypt() as m:
        yield m
    source.use_source(None)


def use_mock(mock, token=None):
    source.use_source(source.QryptSource(mock.url, token or mock.token, 5.0))


def purposes(db_url):
    with psycopg.connect(db_url) as conn:
        return [r[0] for r in conn.execute("SELECT purpose FROM batches ORDER BY requested_at")]


async def test_pool_start_ok(mock, db):
    use_mock(mock)
    pool = Pool()
    await pool.start()
    assert mock.sizes()[0] == "1"
    assert purposes(db)[0] == "startup_check"
    status = pool.status()
    assert status.upstream_status == "ok" and status.last_success_at is not None
    for _ in range(100):  # the refill loop begins after the check
        if pool.level > KIB:
            break
        await asyncio.sleep(0.05)
    assert pool.level > 0 and "96" in mock.sizes()
    await pool.stop()


async def test_pool_start_401(mock, db, caplog):
    use_mock(mock, token="expired-token")
    pool = Pool()
    await pool.start()  # returns: the process keeps running
    assert pool.status().upstream_status == "misconfigured"
    record = next(r for r in caplog.records if r.name == "qrypt_entropy.pool")
    assert '"event": "pool_startup_check_failed"' in record.getMessage()
    assert "Qrypt access token is invalid or expired" in record.getMessage()
    await pool.stop()


async def test_pool_misconfigured_take(mock, db):
    use_mock(mock, token="expired-token")
    pool = Pool()
    await pool.start()
    with pytest.raises(PoolStarved) as err:
        await pool.take(10)
    assert err.value.code == "upstream_misconfigured"
    await pool.stop()


class Stubs:
    def __init__(self, monkeypatch):
        self.error: Exception | None = None
        monkeypatch.setattr(meter, "reserve", lambda kib, purpose, src: Batch(str(uuid.uuid4()), kib))
        monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: None)
        monkeypatch.setattr(source, "fetch", self.fetch)

    async def fetch(self, size_kib):
        if self.error:
            raise self.error
        return Fetched("stub", bytes(size_kib * KIB))


@pytest.fixture
def stubs(monkeypatch):
    return Stubs(monkeypatch)


async def started_pool(stubs, low=2 * KIB, high=4 * KIB):
    pool = Pool(low_mark_bytes=low, high_mark_bytes=high)
    await pool.start()
    for _ in range(200):
        if pool.level >= high:
            break
        await asyncio.sleep(0.01)
    return pool


async def test_pool_status_degraded(stubs):
    pool = await started_pool(stubs)
    stubs.error = Unavailable("down")
    await pool.take(3 * KIB + 1)  # below the low mark: a refill starts, fails once
    for _ in range(100):
        if pool._error is not None:
            break
        await asyncio.sleep(0.01)
    assert pool.level > 0
    assert pool.status().upstream_status == "degraded"
    await pool.stop()


async def test_pool_status_failing(stubs):
    pool = await started_pool(stubs)
    stubs.error = Unavailable("down")
    await pool.take(pool.level)
    with pytest.raises(PoolStarved):
        await pool.take(10)
    assert pool.status().upstream_status == "failing"
    await pool.stop()


async def test_pool_status_recovers(stubs, monkeypatch):
    monkeypatch.setattr(pool_module, "HARD_BACKOFF_S", 0.3)
    pool = await started_pool(stubs)
    stubs.error = Unavailable("down")
    await pool.take(pool.level)
    with pytest.raises(PoolStarved):
        await pool.take(10)
    stubs.error = None
    await asyncio.sleep(0.35)  # after a streak of failures only a waiting take starts the next attempt
    await pool.take(10)
    async with asyncio.timeout(10):
        while pool.status().upstream_status != "ok":
            await asyncio.sleep(0.05)
    assert pool.status().last_success_at is not None
    await pool.stop()


async def test_pool_restart_clears_state(stubs):
    pool = await started_pool(stubs)
    stubs.error = Unavailable("down")
    await pool.take(pool.level)
    with pytest.raises(PoolStarved):
        await pool.take(10)
    await pool.stop()
    stubs.error = None
    await pool.start()
    assert pool.status().upstream_status == "ok"
    async with asyncio.timeout(10):  # the refill loop runs again after a restart
        while pool.level < pool.high:
            await asyncio.sleep(0.01)
    await pool.stop()


async def test_pool_restart_shows_new_error(stubs):
    pool = await started_pool(stubs)
    stubs.error = Unavailable("down")
    await pool.take(pool.level)
    with pytest.raises(PoolStarved):
        await pool.take(10)
    await pool.stop()
    stubs.error = Misconfigured("bad token")
    await pool.start()
    assert pool.status().upstream_status == "misconfigured"
    await pool.stop()


async def test_pool_stop_during_startup_check_adds_nothing(stubs, monkeypatch):
    marks = []
    monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: marks.append(outcome))
    pool = Pool(low_mark_bytes=2 * KIB, high_mark_bytes=4 * KIB)
    gate = asyncio.Event()

    async def slow_fetch(size_kib):
        await gate.wait()
        return Fetched("stub", bytes(size_kib * KIB))

    monkeypatch.setattr(source, "fetch", slow_fetch)
    starting = asyncio.create_task(pool.start())
    await asyncio.sleep(0.05)
    await pool.stop()
    gate.set()
    await starting
    assert pool.level == 0 and marks == ["failed"]


async def test_pool_status_failing_with_waiting_take_and_bytes(stubs):
    pool = await started_pool(stubs)
    await pool.take(2 * KIB)  # leaves 2 KiB, at the low mark: no refill yet
    stubs.error = Unavailable("down")
    waiting = asyncio.create_task(pool.take(3 * KIB))  # more than it holds: the take waits
    for _ in range(100):
        if pool._error is not None:
            break
        await asyncio.sleep(0.01)
    assert pool.level > 0 and pool.status().upstream_status == "failing"
    with pytest.raises(PoolStarved):
        await waiting
    await pool.stop()


async def test_startup_401_stops_requests_until_a_take_asks(mock, db):
    use_mock(mock, token="expired-token")
    pool = Pool()
    await pool.start()
    await asyncio.sleep(0.5)
    assert mock.sizes() == ["1"] and purposes(db) == ["startup_check"]  # no refill loop, no 64 KiB reserve
    for _ in range(3):
        with pytest.raises(PoolStarved):
            await pool.take(10)
    await asyncio.sleep(1.5)  # a retry loop would have kept asking
    assert len(mock.sizes()) <= 1 + 3  # at most one attempt per take
    assert pool.status().upstream_status == "misconfigured"
    await pool.stop()
