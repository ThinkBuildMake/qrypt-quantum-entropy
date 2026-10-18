"""S09 gate cases: backoff after 429 and the 30-per-10-s ceiling (docs/stories/S09-source-limits.md AC1-AC6)."""

import asyncio
import logging
import random
import time

import httpx
import pytest

from qrypt_entropy.errors import RateLimited
from qrypt_entropy.upstream import source
from tests.mock_qrypt import MockQrypt

NOMINAL = [0.5, 1, 2, 4, 8, 16, 32, 60]


class VirtualClock:
    """One caller only: sleeping moves the clock forward at once and records the wait."""

    def __init__(self) -> None:
        self.now, self.waits = 1000.0, []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
async def world():
    clock = VirtualClock()
    async with MockQrypt(clock=clock) as mock:
        yield mock, clock


def client(mock, clock, seed=1):
    return source.QryptSource(mock.url, mock.token, clock=clock, sleep=clock.sleep, rng=random.Random(seed))


async def run_exhausted(mock, clock, seed=1):
    mock.faults = [429] * 100
    with pytest.raises(RateLimited):
        await client(mock, clock, seed).fetch(1)
    return list(clock.waits)


async def test_backoff_nominal(world, monkeypatch):
    mock, clock = world
    monkeypatch.setattr(random.Random, "uniform", lambda self, a, b: 1.0)  # no jitter: the nominal waits
    assert await run_exhausted(mock, clock) == NOMINAL


async def test_backoff_jitter(world):
    mock, clock = world
    first = await run_exhausted(mock, clock, seed=1)
    clock.waits.clear()
    second = await run_exhausted(mock, clock, seed=2)
    for run in (first, second):
        assert len(run) == len(NOMINAL)
        assert all(0.5 * n <= w <= n for w, n in zip(run, NOMINAL, strict=True))
    assert first != second


async def test_backoff_60s(world):
    mock, clock = world
    await run_exhausted(mock, clock)  # 429 for longer than 120 s on the test clock
    assert sum(1 for r in mock.log if r.at - mock.log[0].at < 60) <= 8
    assert {r.status for r in mock.log} == {429}


async def test_rate_limited_raised(world, caplog):
    mock, clock = world
    mock.faults = [429] * 100
    with pytest.raises(RateLimited) as err, caplog.at_level(logging.INFO):
        await client(mock, clock).fetch(1)
    assert err.value.code == "temporarily_unavailable"
    assert len(mock.log) == 9
    assert '"attempt": 9' in caplog.text
    assert "upstream_rate_limited" in caplog.text


async def test_retry_same_batch(world):
    mock, clock = world
    mock.faults = [429]
    got = await client(mock, clock).fetch(2)
    assert len(got.bytes) == 2048
    assert [r.status for r in mock.log] == [429, 200]
    assert mock.sizes() == ["2", "2"]


async def test_rate_ceiling_100():
    scale = 5.0  # 5 test seconds pass per real second
    t0 = time.monotonic()

    def clock() -> float:
        return (time.monotonic() - t0) * scale

    async def sleep(seconds: float) -> None:
        await asyncio.sleep(seconds / scale)

    async with MockQrypt(clock=clock) as mock:
        mock.override = lambda i: 429 if i in (3, 17, 40) else None  # the mock's own limit still applies
        c = source.QryptSource(mock.url, mock.token, clock=clock, sleep=sleep, rng=random.Random(3))
        results = await asyncio.gather(*(c.fetch(1) for _ in range(100)))
    assert len(results) == 100
    times = sorted(r.at for r in mock.log)
    assert len(times) >= 103
    busiest = max(sum(1 for u in times if t <= u < t + 10) for t in times)
    assert busiest <= 30


def test_token_setting_accepts_both_names(monkeypatch):
    from qrypt_entropy.config import Settings

    monkeypatch.delenv("QRYPT_API_KEY", raising=False)  # the suite points it at its mock
    monkeypatch.setenv("QRYPT_API_TOKEN", "old-name")
    assert Settings(_env_file=None).qrypt_api_token.get_secret_value() == "old-name"
    monkeypatch.setenv("QRYPT_API_KEY", "new-name")
    assert Settings(_env_file=None).qrypt_api_token.get_secret_value() == "new-name"


async def test_mock_answers_429_to_the_31st_request_in_10s():
    async with MockQrypt(clock=lambda: 5.0) as mock, httpx.AsyncClient() as raw:  # a frozen clock: one window
        for _ in range(31):
            await raw.get(
                f"{mock.url}/api/v1/quantum-entropy?size=1", headers={"Authorization": f"Bearer {mock.token}"}
            )
    assert [r.status for r in mock.log] == [200] * 30 + [429]
