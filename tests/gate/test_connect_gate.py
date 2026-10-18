"""S06 gate cases: revoke-next-call, bad-key-draws-nothing (AZ-2). A refused call touches neither pool nor meter.

Since S20 the pool is a real `Pool` installed as the process-wide one and the meter is real: the cases measure the pool
level, `used_kib` and the row counts before and after instead of tripping a wire.
"""

import json

import psycopg
import pytest
from mcp.client import Client

from qrypt_entropy.service import access, pool
from qrypt_entropy.transport.mcp_server import build_server

ARGS = {"length": 8, "encoding": "hex"}
BATCH = "00000000-0000-4000-8000-0000000000b1"


@pytest.fixture
def untouched(db):
    """A pool holding 4096 bytes of a real batch row and a meter at 64 KiB; `state` measures all of them."""
    held = pool.Pool(low_mark_bytes=0, high_mark_bytes=8192)  # no refill can start
    held.add(BATCH, bytes(4096))
    pool.use_pool(held)
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE budget SET used_kib = 64")
        conn.execute("INSERT INTO batches (id, purpose, kib, outcome) VALUES (%s, 'refill', 64, 'received')", (BATCH,))

    def state():
        with psycopg.connect(db) as conn:
            used = conn.execute("SELECT used_kib FROM budget").fetchone()[0]
            batches = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
            issued = conn.execute("SELECT count(*) FROM issuances").fetchone()[0]
        return held.level, used, batches, issued

    assert state() == (4096, 64, 1, 0)
    return state


async def test_gate_revoke_next_call(db, untouched):
    principal, key = access.create_credential("client", "soon-revoked")
    async with Client(build_server(lambda: key, run_pool=False)) as client:
        assert not (await client.call_tool("get_random_bytes", ARGS)).is_error  # connected and working
        assert untouched() == (4088, 64, 1, 1)  # the one allowed call drew 8 bytes and left one audit row
        access.revoke_client(principal.client_id)
        result = await client.call_tool("get_random_bytes", ARGS)
    assert result.is_error and json.loads(result.content[0].text)["error"] == "unauthorized"
    assert untouched() == (4088, 64, 1, 1)


@pytest.mark.parametrize("key", ["never-issued", "", None])
async def test_gate_bad_key_draws_nothing(db, untouched, key):
    async with Client(build_server(lambda: key, run_pool=False)) as client:
        result = await client.call_tool("get_random_bytes", ARGS)
    assert result.is_error and json.loads(result.content[0].text)["error"] == "unauthorized"
    assert untouched() == (4096, 64, 1, 0)
