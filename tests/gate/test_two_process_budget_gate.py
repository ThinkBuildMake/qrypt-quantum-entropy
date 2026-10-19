"""S39 gate cases: the budget holds across processes and 429 retries (docs/stories/S39-robust-faults.md AC2, AC3).

Two real MCP server processes on one database against one mock Qrypt; the in-process pool for the retry case.
`make two-process-budget` runs this file.
"""

import asyncio
import json

import psycopg
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import BudgetExhausted
from qrypt_entropy.service import access, meter, pool
from tests.mock_qrypt import MockQrypt
from tests.test_mcp_connect import launch

CAP_KIB = 1000
CALLERS = 100
LENGTH = 1024


def rows(url, query):
    with psycopg.connect(url) as conn:
        return conn.execute(query).fetchall()


def launched(tmp_path, key, name, mock):
    params = launch(tmp_path / name, key)
    params.env.update(QRYPT_BASE_URL=mock.url, QRYPT_API_KEY=mock.token)
    return params


async def test_two_processes_share_one_cap(db, tmp_path):
    """AC2: 100 callers across two server processes, driven until refused: 1000 KiB or fewer are granted, and the
    meter equals the KiB the mock served or refused with anything but 429."""
    meter.set_budget(CAP_KIB)
    _, key = access.create_credential("client", "two-process")
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    refused, issued = [], []

    async def caller(client):
        for _ in range(2000):
            result = await client.call_tool("get_random_bytes", {"length": LENGTH, "encoding": "hex"})
            if not result.is_error:
                issued.append(len(result.structured_content["value"]) // 2)
                continue
            error = json.loads(result.content[0].text)["error"]
            if error == "budget_exhausted":
                refused.append(error)
                return
            assert error == "temporarily_unavailable", error

    async with MockQrypt() as mock:
        async with Client(launched(tmp_path, key, "a", mock)) as one, Client(launched(tmp_path, key, "b", mock)) as two:
            await asyncio.gather(*(caller(one if i % 2 else two) for i in range(CALLERS)))
    assert len(refused) == CALLERS
    [(used,)] = rows(db, "SELECT used_kib FROM budget")
    assert used <= CAP_KIB
    assert used == sum(int(r.size) for r in mock.log if r.status != 429)
    assert rows(db, "SELECT sum(kib) FROM batches") == [(used,)]
    assert 0 < sum(issued) <= used * 1024
    assert rows(db, "SELECT count(DISTINCT source) FROM batches") == [(1,)]


async def test_retry_one_batch(db, monkeypatch):
    """AC3 retry-one-batch: the mock answers 429 once and then 200; the refill is one batch counted once."""
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        get_settings.cache_clear()
        mock.faults = [429]
        taken = await pool.get_pool().take(8)  # no startup check: this take starts the first refill
    get_settings.cache_clear()
    assert len(taken.bytes) == 8
    assert rows(db, "SELECT purpose, kib, outcome FROM batches") == [("refill", 96, "received")]
    assert rows(db, "SELECT used_kib FROM budget") == [(96,)]
    assert [r.status for r in mock.log] == [429, 200] and mock.sizes() == ["96", "96"]


def _race(ask, threads):
    """`threads` callers reserve `ask` KiB at once; returns the KiB granted to each."""
    import threading

    start, granted = threading.Barrier(threads), []

    def reserve():
        start.wait()
        try:
            granted.append(meter.reserve(ask, "refill", "qrypt").kib)
        except BudgetExhausted:
            pass

    workers = [threading.Thread(target=reserve) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return granted


def test_concurrent_reserves_never_pass_the_cap(db):
    """The row lock in `meter.reserve` is what keeps the cap: many callers at once, each wanting more than the room
    left (a REFILL_KIB-sized ask against a small cap), several rounds. Without `FOR UPDATE` they all read the same
    `used_kib` and the sum of grants passes the cap."""
    room, ask = 100, 96
    for _ in range(5):
        [(before,)] = rows(db, "SELECT used_kib FROM budget")
        meter.set_budget(before + room)  # used_kib only rises, so each round gets fresh room
        granted = _race(ask, 16)
        assert sum(granted) <= room
        assert rows(db, "SELECT used_kib FROM budget") == [(before + sum(granted),)]
