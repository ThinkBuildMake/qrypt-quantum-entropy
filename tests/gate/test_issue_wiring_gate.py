"""S20 gate cases: the issue flow joined end to end (docs/stories/S20-w-issue.md).

Real Postgres, the real Access, Pool, Meter, Entropy source and Issuance, and the committed mock Qrypt. The keys come
from `scripts/seed.py` (`make seed`), as they do for a person. Nothing here replaces a module with a stand-in; the one
injected fault (AC7) is a trigger in the database itself. AC9 (a live Qrypt token) is a human step: docs/DEPLOY.md.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access, pool
from qrypt_entropy.transport.http_app import app
from qrypt_entropy.transport.mcp_server import build_server
from tests.mock_qrypt import MockQrypt
from tests.test_mcp_connect import TOOLS, launch, refusal

KIB = 1024
HEX = {"length": 16, "encoding": "hex"}


def rows(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


@pytest.fixture
async def wired(db, monkeypatch, tmp_path):
    """A migrated database seeded by `scripts/seed.py`, with the process pointed at its own mock Qrypt."""
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        get_settings.cache_clear()
        seeded = subprocess.run(
            [sys.executable, "scripts/seed.py"], env=os.environ.copy(), capture_output=True, text=True, check=True
        ).stdout
        keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded))
        yield SimpleNamespace(db=db, mock=mock, keys=keys, key=keys["billing-agent"], tmp=tmp_path)
    get_settings.cache_clear()


async def warm() -> None:
    """Wait for the first refill, so what is drawn next comes from a 64 KiB batch (PF-1 measures a warm pool)."""
    for _ in range(300):
        if pool.get_pool().level > 60 * KIB:
            return
        await asyncio.sleep(0.02)
    pytest.fail("the pool did not fill")


def body(result):
    return result.structured_content if not result.is_error else json.loads(result.content[0].text)


async def test_connect_real_access(wired):
    """AC1: the seed key connects to a real server process, and the call is attributed to its client."""
    async with Client(launch(wired.tmp, wired.key)) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS
        result = await client.call_tool("get_random_bytes", HEX)
    ref = body(result)["metadata"]["reference"]
    name = "SELECT c.name FROM issuances i JOIN credentials c ON c.id = i.client_id WHERE i.id = %s"
    assert rows(wired.db, name, ref) == [("billing-agent",)]
    assert rows(wired.db, "SELECT purpose, kib, outcome FROM batches ORDER BY requested_at")[0] == (
        "startup_check", 1, "received",
    )  # fmt: skip


async def test_revoked_key_refused_real(wired):
    """AC2: a revoked key is refused where the stub accepted every key; nothing is listed, nothing is spent."""
    principal, key = access.create_credential("client", "throwaway")
    access.revoke_client(principal.client_id)
    assert (await refusal(wired.tmp, key))["error"] == "unauthorized"
    assert wired.mock.log == []  # refused before the pool started
    assert rows(wired.db, "SELECT count(*) FROM batches") == [(0,)]


def test_http_real_keys(wired):
    """AC3: the seed admin key, the seed client key and no key on the hosted admin route."""
    http = TestClient(app)
    asked = [
        http.get("/admin/usage", headers={"Authorization": f"Bearer {wired.keys[who]}"})
        for who in ("admin", "billing-agent")
    ]
    asked.append(http.get("/admin/usage"))
    assert [r.status_code for r in asked] == [200, 403, 401]
    assert [r.json().get("error") for r in asked[1:]] == ["forbidden", "unauthorized"]


async def test_issue_bytes_real_pool(wired):
    """AC4: bytes from the real pool, with their source and a 64 KiB batch that was received."""
    async with Client(build_server(lambda: wired.key)) as client:
        await warm()
        result = await client.call_tool("get_random_bytes", HEX)
    data = body(result)
    meta = data["metadata"]
    assert re.fullmatch(r"[0-9a-f]{32}", data["value"])
    assert (meta["source"], meta["bytes_consumed"]) == ("qrypt", 16) and meta["reference"]
    assert rows(wired.db, "SELECT kib >= 64, outcome FROM batches WHERE id = %s", meta["batch_id"]) == [
        (True, "received")
    ]
    assert sorted(r[0] for r in rows(wired.db, "SELECT purpose FROM batches")) == ["refill", "startup_check"]


async def test_issue_int_real_pool(wired):
    """AC5: an integer inside the range, its audit record summing to bytes_consumed."""
    async with Client(build_server(lambda: wired.key)) as client:
        await warm()
        results = [body(await client.call_tool("random_int", {"min": 1, "max": 6})) for _ in range(20)]
    assert all(isinstance(r["value"], int) and 1 <= r["value"] <= 6 for r in results)
    for r in results:
        ref = r["metadata"]["reference"]
        audit = rows(
            wired.db,
            "SELECT i.tool, i.bytes_consumed,"
            " (SELECT sum(byte_count)::int FROM issuance_batches WHERE issuance_id = i.id)"
            " FROM issuances i WHERE i.id = %s",
            ref,
        )
        assert audit == [("random_int", r["metadata"]["bytes_consumed"], r["metadata"]["bytes_consumed"])]


async def test_issue_token_real_store(wired):
    """AC6: a redeemable token is stored as a hash, tied to its issuance."""
    args = {"kind": "otp_6", "owner": "user-42", "expiry_seconds": 600}
    async with Client(build_server(lambda: wired.key)) as client:
        await warm()
        data = body(await client.call_tool("generate_token", args))
    assert re.fullmatch(r"\d{6}", data["value"])
    stored = rows(wired.db, "SELECT owner, issuance_id::text, token_hash FROM tokens WHERE owner = 'user-42'")
    assert [(r[0], r[1]) for r in stored] == [("user-42", data["metadata"]["reference"])]
    assert len(bytes(stored[0][2])) == 32 and data["value"].encode() not in bytes(stored[0][2])


async def test_issue_token_store_fault(wired):
    """AC7: a refused token insert fails the whole call across the real seam: no value, no issuance row."""
    with psycopg.connect(wired.db, autocommit=True) as conn:
        conn.execute(
            "CREATE FUNCTION refuse_token() RETURNS trigger LANGUAGE plpgsql"
            " AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$"
        )
        conn.execute("CREATE TRIGGER refuse BEFORE INSERT ON tokens FOR EACH ROW EXECUTE FUNCTION refuse_token()")
    async with Client(build_server(lambda: wired.key)) as client:
        await warm()
        result = await client.call_tool("generate_token", {"kind": "otp_6", "owner": "user-42"})
        assert result.is_error and body(result)["error"] == "temporarily_unavailable"
        assert "value" not in body(result)
        for table in ("issuances", "issuance_batches"):
            assert rows(wired.db, f"SELECT count(*) FROM {table}") == [(0,)]
        assert rows(wired.db, "SELECT count(*) FROM tokens WHERE owner = 'user-42'") == [(0,)]
        assert not (await client.call_tool("generate_token", {"kind": "otp_6"})).is_error  # nothing to store


async def test_issue_invalid_no_draw(wired):
    """AC8: bad input is explained, and no byte was drawn."""
    async with Client(build_server(lambda: wired.key)) as client:
        await warm()
        level = pool.get_pool().level
        requests = len(wired.mock.log)
        result = await client.call_tool("get_random_bytes", {"length": 0, "encoding": "hex"})
        assert pool.get_pool().level == level and len(wired.mock.log) == requests
    assert result.is_error and body(result)["error"] == "invalid_argument" and "length" in body(result)["message"]
    assert rows(wired.db, "SELECT count(*) FROM issuances") == [(0,)]


async def test_fail_closed_503(wired):
    """AC10: Qrypt down and the pool empty: within 2 s a plain error, no value, no issuance row."""
    wired.mock.sticky = 503
    async with Client(build_server(lambda: wired.key)) as client:
        started = time.monotonic()
        result = await client.call_tool("get_random_bytes", HEX)
        elapsed = time.monotonic() - started
    assert result.is_error and body(result)["error"] == "temporarily_unavailable" and "value" not in body(result)
    assert 1.7 < elapsed < 2.0, elapsed  # RL-1: within 2 s
    assert rows(wired.db, "SELECT count(*) FROM issuances") == [(0,)]
    assert {r[0] for r in rows(wired.db, "SELECT outcome FROM batches")} <= {"failed"}


async def test_slow_startup_check_does_not_hold_initialize(wired, monkeypatch):
    """The startup check is waited for at most STARTUP_WAIT_S; it finishes in the background."""
    from qrypt_entropy.transport import mcp_server

    monkeypatch.setattr(mcp_server, "STARTUP_WAIT_S", 0.3)
    wired.mock.delay_s = 1.0
    started = time.monotonic()
    async with Client(build_server(lambda: wired.key)) as client:
        assert time.monotonic() - started < 0.9  # initialize did not wait for the 1 s answer
        await asyncio.sleep(1.5)
        assert body(await client.call_tool("get_random_bytes", HEX))["metadata"]["source"] == "qrypt"
    assert rows(wired.db, "SELECT purpose, outcome FROM batches ORDER BY requested_at")[0] == (
        "startup_check",
        "received",
    )


async def test_cancelled_startup_leaves_no_requested_batch(wired):
    """Stopping the server while the check is in flight settles its batch (no row stays `requested`)."""
    wired.mock.delay_s = 3.0
    async with Client(build_server(lambda: wired.key)):
        await asyncio.sleep(0.3)
    await asyncio.sleep(0.3)
    assert rows(wired.db, "SELECT outcome FROM batches WHERE outcome = 'requested'") == []
