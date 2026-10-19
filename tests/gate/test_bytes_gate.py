"""S13 gate cases: get_random_bytes and its audit record (docs/stories/S13-iss-bytes.md AC1-AC9).

The pool is a real `Pool` holding seeded bytes whose batches exist in `batches`; Access is a seeded credential.
"""

import json
import logging
import uuid

import psycopg
import pytest
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import BudgetExhausted, InvalidArgument, PoolStarved, TemporarilyUnavailable
from qrypt_entropy.models import BatchSlice, Principal, Taken
from qrypt_entropy.service import issuance, meter, pool
from qrypt_entropy.service.access import hash_key
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.storage.db import close_pools as close_db_pools
from qrypt_entropy.transport.mcp_server import build_server

GATE_KEY = "bytes-gate-client-key"  # a test-only key, hashed into the fresh database below
CLIENT = str(uuid.uuid4())
PRINCIPAL = Principal(CLIENT, "client", "agent-1")


@pytest.fixture
def bdb(db):
    """The shared migrated database plus a client credential and fresh call statistics."""
    with psycopg.connect(db) as conn:
        conn.execute(
            "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, 'agent-1', 'client', %s)",
            (CLIENT, b"k" * 32),
        )
        conn.execute(
            "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, 'gate-client', 'client', %s)",
            (str(uuid.uuid4()), hash_key(GATE_KEY)),
        )
    issuance.reset_call_stats()
    yield db
    close_db_pools()
    meter.close_pools()


def seed_batch(url, kib=1) -> str:
    batch_id = str(uuid.uuid4())
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO batches (id, purpose, kib) VALUES (%s, 'refill', %s)", (batch_id, kib))
    return batch_id


@pytest.fixture
def stub_pool(bdb, monkeypatch):
    """A real Pool installed as the process-wide one, with no refill; `serve` adds bytes from a freshly seeded batch."""
    p = Pool(low_mark_bytes=0, high_mark_bytes=0)
    pool.use_pool(p)
    p.serve = lambda data: (batch := seed_batch(bdb), p.add(batch, data))[0]
    return p


def rows(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


async def test_bytes_hex_with_metadata_and_audit_row(bdb, stub_pool):
    batch = stub_pool.serve(bytes([0, 1, 2, 3]))
    result = await issuance.get_random_bytes(PRINCIPAL, 4, "hex")
    assert result.value == "00010203"
    meta = result.metadata
    assert (meta.source, meta.batch_id, meta.batch_ids, meta.bytes_consumed) == ("qrypt", batch, [batch], 4)
    issued = rows(bdb, "SELECT id::text, client_id::text, tool, bytes_consumed FROM issuances")
    assert issued == [(meta.reference, CLIENT, "get_random_bytes", 4)]
    assert rows(bdb, "SELECT batch_id::text, byte_offset, byte_count FROM issuance_batches") == [(batch, 0, 4)]
    dump = json.dumps(rows(bdb, "SELECT i::text FROM issuances i UNION ALL SELECT b::text FROM issuance_batches b"))
    assert "00010203" not in dump and "AAECAw" not in dump


async def test_bytes_base64(bdb, stub_pool):
    stub_pool.serve(bytes([0, 1, 2]))
    assert (await issuance.get_random_bytes(PRINCIPAL, 3, "base64")).value == "AAEC"


async def test_bytes_two_batches(bdb, stub_pool):
    a, b = stub_pool.serve(b"abc"), stub_pool.serve(b"de")
    result = await issuance.get_random_bytes(PRINCIPAL, 5, "hex")
    assert result.value == b"abcde".hex()
    assert result.metadata.batch_id == a and result.metadata.batch_ids == [a, b]
    got = rows(bdb, "SELECT batch_id::text, byte_offset, byte_count FROM issuance_batches ORDER BY byte_count DESC")
    assert got == [(a, 0, 3), (b, 0, 2)]
    assert rows(bdb, "SELECT bytes_consumed FROM issuances") == [(5,)]


@pytest.mark.parametrize(
    ("length", "encoding", "field"), [(0, "hex", "length"), (1025, "hex", "length"), (4, "binary", "encoding")]
)
async def test_bytes_invalid_input(bdb, stub_pool, length, encoding, field):
    stub_pool.serve(bytes(4096))
    with pytest.raises(InvalidArgument, match=field):
        await issuance.get_random_bytes(PRINCIPAL, length, encoding)
    assert stub_pool.level == 4096
    assert rows(bdb, "SELECT used_kib FROM budget") == [(0,)]
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]


@pytest.mark.parametrize(
    "failure",
    [
        TemporarilyUnavailable("empty"),
        BudgetExhausted("spent"),
        PoolStarved("starved", "budget_exhausted"),
        PoolStarved("starved"),
    ],
)
async def test_bytes_pool_failure(bdb, monkeypatch, failure):
    async def fail(n):
        raise failure

    monkeypatch.setattr(pool, "take", fail)
    with pytest.raises(type(failure)) as caught:
        await issuance.get_random_bytes(PRINCIPAL, 4, "hex")
    assert caught.value.code == failure.code
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]


async def test_bytes_audit_fault_releases_no_value(bdb, stub_pool):
    stub_pool.serve(bytes([1, 2, 3, 4, 5, 6, 7, 8]))
    unknown = Principal(str(uuid.uuid4()), "client", "ghost")  # the foreign key on issuances.client_id refuses it
    with pytest.raises(TemporarilyUnavailable) as caught:
        await issuance.get_random_bytes(unknown, 4, "hex")
    assert "01020304" not in caught.value.message
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]
    assert (await issuance.get_random_bytes(PRINCIPAL, 4, "hex")).value == "05060708"  # 01..04 were discarded
    assert issuance.call_stats()["errorsByCode"] == {"temporarily_unavailable": 1}


async def test_bytes_log_and_stats(bdb, stub_pool, caplog):
    stub_pool.serve(bytes(range(30)))
    caplog.set_level(logging.INFO)
    async with Client(build_server(lambda: GATE_KEY, run_pool=False)) as client:
        results = [await client.call_tool("get_random_bytes", {"length": 4, "encoding": "hex"}) for _ in range(3)]
        bad = await client.call_tool("get_random_bytes", {"length": 0, "encoding": "hex"})
    assert [r.is_error for r in results] == [False] * 3 and bad.is_error
    lines = [json.loads(r.getMessage()) for r in caplog.records if '"tool_call"' in r.getMessage()]
    assert len(lines) == 4 and all(isinstance(line["duration_ms"], float) for line in lines)
    secret = results[0].structured_content["value"]
    assert all(secret not in r.getMessage() for r in caplog.records)
    stats = issuance.call_stats()
    assert stats["callsByTool"] == {"get_random_bytes": 3}
    assert stats["errorsByCode"] == {"invalid_argument": 1}
    assert stats["toolCallDurationMs"]["p95"] > 0
    assert (bad.is_error, json.loads(bad.content[0].text)["error"]) == (True, "invalid_argument")


async def test_bytes_max_config(bdb, stub_pool, monkeypatch):
    stub_pool.serve(bytes(128))
    monkeypatch.setenv("PER_CALL_MAX_BYTES", "64")
    get_settings.cache_clear()
    with pytest.raises(InvalidArgument, match="64"):
        await issuance.get_random_bytes(PRINCIPAL, 65, "hex")
    assert stub_pool.level == 128
    assert len((await issuance.get_random_bytes(PRINCIPAL, 64, "hex")).value) == 128


async def test_bytes_batch_row_failure_rolls_back_the_issuance(bdb, monkeypatch):
    async def take(n):  # a slice naming a batch that does not exist: the second insert fails
        return Taken(b"\x01\x02", [BatchSlice(str(uuid.uuid4()), 0, 2)])

    monkeypatch.setattr(pool, "take", take)
    with pytest.raises(TemporarilyUnavailable) as caught:
        await issuance.get_random_bytes(PRINCIPAL, 2, "hex")
    assert "0102" not in caught.value.message
    assert rows(bdb, "SELECT count(*) FROM issuances") == [(0,)]


async def test_bytes_schema_rejection_has_duration_and_is_counted(bdb, stub_pool, caplog):
    stub_pool.serve(bytes(8))
    insert_ok = {"length": 4, "encoding": "hex"}
    caplog.set_level(logging.INFO)
    async with Client(build_server(lambda: GATE_KEY, run_pool=False)) as client:
        ok = await client.call_tool("get_random_bytes", insert_ok)
        bad = await client.call_tool("get_random_bytes", {"length": 4, "encoding": "binary"})
    assert not ok.is_error and bad.is_error
    assert json.loads(bad.content[0].text)["error"] == "invalid_argument"
    lines = [json.loads(r.getMessage()) for r in caplog.records if '"tool_call"' in r.getMessage()]
    assert len(lines) == 2 and all(isinstance(line["duration_ms"], float) for line in lines)
    stats = issuance.call_stats()
    assert stats["callsByTool"] == {"get_random_bytes": 1} and stats["errorsByCode"] == {"invalid_argument": 1}
