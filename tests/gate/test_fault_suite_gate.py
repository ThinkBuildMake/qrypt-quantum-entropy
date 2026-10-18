"""S39 gate cases: every upstream fault fails closed through the real service (docs/stories/S39-robust-faults.md).

Real Postgres, the real Access, Pool, Meter, Entropy source and Issuance, with the faults injected by the committed
mock Qrypt. `make fault-suite` runs this file. The two-process budget cases are in test_two_process_budget_gate.py.
"""

import asyncio
import json
import time

import psycopg
import pytest
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access
from qrypt_entropy.transport.mcp_server import build_server
from qrypt_entropy.upstream import source
from tests.mock_qrypt import MockQrypt

HEX = {"length": 16, "encoding": "hex"}
TOKEN = {"kind": "otp_6"}


def rows(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def body(result):
    return json.loads(result.content[0].text) if result.is_error else result.structured_content


@pytest.fixture
async def world(db, monkeypatch):
    """A migrated database, a client key, and a process pointed at its own mock Qrypt (pool empty)."""
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        get_settings.cache_clear()
        _, key = access.create_credential("client", "fault-client")
        yield type("World", (), {"db": db, "mock": mock, "key": key})
    get_settings.cache_clear()


def inject(mock, fault):
    """Make the mock misbehave on every request from now on."""
    if fault == "drop":
        mock.faults = ["drop"] * 200
    elif fault == "malformed":
        mock.faults = ["malformed"] * 200
    elif fault == "slow":
        mock.delay_s = 6.0
    else:
        mock.sticky = fault


FAULTS = [
    (403, "budget_exhausted"),
    (500, "temporarily_unavailable"),
    (503, "temporarily_unavailable"),
    ("slow", "temporarily_unavailable"),
    ("drop", "temporarily_unavailable"),
    ("malformed", "temporarily_unavailable"),
    (401, "upstream_misconfigured"),
    (429, "temporarily_unavailable"),
]


@pytest.mark.parametrize(("fault", "code"), FAULTS, ids=[str(f) for f, _ in FAULTS])
@pytest.mark.parametrize("tool", ["get_random_bytes", "random_int", "generate_token"])
async def test_fault_fails_closed(world, fault, code, tool):
    """AC1: pool drained and the mock misbehaving: the right code, no value, no audit row, nothing but qrypt."""
    args = {"get_random_bytes": HEX, "random_int": {"min": 1, "max": 6}, "generate_token": TOKEN}[tool]
    inject(world.mock, fault)
    async with Client(build_server(lambda: world.key)) as client:
        started = time.monotonic()
        result = await client.call_tool(tool, args)
        elapsed = time.monotonic() - started
    data = body(result)
    assert result.is_error and data["error"] == code
    assert set(data) == {"error", "message"}  # no value, no metadata, plain words
    assert elapsed < 2.2, elapsed  # RL-1
    assert rows(world.db, "SELECT count(*) FROM issuances") == [(0,)]
    assert rows(world.db, "SELECT count(*) FROM tokens") == [(0,)]
    assert {r[0] for r in rows(world.db, "SELECT source FROM batches")} <= {"qrypt"}
    assert {r[0] for r in rows(world.db, "SELECT outcome FROM batches")} <= {"failed", "requested"}
    assert "Traceback" not in data["message"]


async def test_recovers_after_a_fault_clears(world):
    """A transient fault leaves no scar: once Qrypt answers again the next call returns a value from qrypt."""
    world.mock.sticky = 503
    async with Client(build_server(lambda: world.key)) as client:
        assert body(await client.call_tool("get_random_bytes", HEX))["error"] == "temporarily_unavailable"
        world.mock.sticky = None
        for _ in range(40):  # the refill loop pauses up to 10 s after a failure; a take wakes it only at its next try
            result = await client.call_tool("get_random_bytes", HEX)
            if not result.is_error:
                break
        assert not result.is_error and body(result)["metadata"]["source"] == "qrypt"


async def test_second_source_label(world):
    """second-source-label (AC4): a plugged-in test_source labels the metadata and the batch, and Qrypt is not asked."""
    source.use_source(source.TestSource())
    async with Client(build_server(lambda: world.key)) as client:
        for _ in range(100):
            result = await client.call_tool("get_random_bytes", HEX)
            if not result.is_error:
                break
            await asyncio.sleep(0.05)
    meta = body(result)["metadata"]
    assert not result.is_error and meta["source"] == "test_source"
    assert rows(world.db, "SELECT source FROM batches WHERE id = %s", meta["batch_id"]) == [("test_source",)]
    assert world.mock.log == []
