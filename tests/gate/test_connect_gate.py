"""S06 gate cases: revoke-next-call, bad-key-draws-nothing (AZ-2). A refused call touches neither."""

import json

import psycopg
import pytest
from mcp.client import Client

from qrypt_entropy.service import access, meter, pool
from qrypt_entropy.transport.mcp_server import build_server

ARGS = {"length": 8, "encoding": "hex"}


@pytest.fixture
def untouched(db, monkeypatch):
    """A pool holding 4096 bytes and a meter at 64 KiB, with tripwires on every way to draw from them."""
    held = pool.Pool(low_mark_bytes=0, high_mark_bytes=8192)
    held.add("00000000-0000-4000-8000-0000000000b1", bytes(4096))
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE budget SET used_kib = 64")
        # the batch the stub pool credits, so the one allowed call in the revoke case can be audited
        conn.execute("INSERT INTO batches (id, purpose, kib) VALUES (%s, 'refill', 64)", (pool.STUB_BATCH_ID,))

    def arm():
        def tripwire(*args, **kwargs):
            pytest.fail("a refused call reached the pool or the meter")

        for target, name in ((pool, "take"), (held, "take"), (meter, "reserve")):
            monkeypatch.setattr(target, name, tripwire)

    def state():
        with psycopg.connect(db) as conn:
            used = conn.execute("SELECT used_kib FROM budget").fetchone()[0]
            batches = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
        return held.level, used, batches

    assert state() == (4096, 64, 1)
    return state, arm


async def test_gate_revoke_next_call(db, untouched):
    state, arm = untouched
    principal, key = access.create_credential("client", "soon-revoked")
    async with Client(build_server(lambda: key)) as client:
        assert not (await client.call_tool("get_random_bytes", ARGS)).is_error  # connected and working
        access.revoke_client(principal.client_id)
        arm()
        result = await client.call_tool("get_random_bytes", ARGS)
    assert result.is_error and json.loads(result.content[0].text)["error"] == "unauthorized"
    assert state() == (4096, 64, 1)


@pytest.mark.parametrize("key", ["never-issued", "", None])
async def test_gate_bad_key_draws_nothing(db, untouched, key):
    state, arm = untouched
    arm()
    async with Client(build_server(lambda: key)) as client:
        result = await client.call_tool("get_random_bytes", ARGS)
    assert result.is_error and json.loads(result.content[0].text)["error"] == "unauthorized"
    assert state() == (4096, 64, 1)
