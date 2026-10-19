"""S33 gate cases: one token life across the real seams (docs/stories/S33-w-redeem.md).

The code is issued by the real stdio MCP server process, redeemed and traced through the real HTTP service (uvicorn in
a subprocess), all on one real Postgres and the committed mock Qrypt. Nothing is replaced by a stand-in.
"""

import asyncio
import os
import re
import socket
import subprocess
import sys
import uuid
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from psycopg import sql
from sqlalchemy.engine import make_url

from qrypt_entropy.config import get_settings
from tests.mock_qrypt import MockQrypt

PEPPER = "s33-test-pepper"  # not a real secret
OWNER = "user-42"
SERVER_KEYS = ("DATABASE_URL", "KEY_HASH_SECRET", "QRYPT_BASE_URL", "QRYPT_API_KEY")


def rows(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def world(db, monkeypatch, tmp_path):
    """Seeded keys, a mock Qrypt, and a uvicorn process serving the HTTP service on the same database."""
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        monkeypatch.setenv("PEPPER", PEPPER)
        monkeypatch.setenv("TOKEN_PEPPER", PEPPER)
        get_settings.cache_clear()
        seeded = subprocess.run(
            [sys.executable, "scripts/seed.py"], env=os.environ.copy(), capture_output=True, text=True, check=True
        ).stdout
        keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded))
        port = free_port()
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(port)],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(200):
                try:
                    httpx.get(f"{base}/health")
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("the HTTP service did not start")
            http = httpx.Client(base_url=base)
            yield SimpleNamespace(db=db, keys=keys, http=http, tmp=tmp_path)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    get_settings.cache_clear()


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def mcp_params(world, key=None):
    env = {"PATH": os.environ["PATH"], "LOG_FILE": str(world.tmp / "server.log"), "PEPPER": PEPPER}
    env |= {k: os.environ[k] for k in SERVER_KEYS if k in os.environ} | {
        "MCP_CLIENT_KEY": key or world.keys["billing-agent"]
    }
    return StdioServerParameters(command=sys.executable, args=["-m", "qrypt_entropy.transport.mcp_server"], env=env)


async def issue(world, expiry_seconds=600, owner=OWNER):
    """A code issued by the real MCP server process: (value, metadata)."""
    args = {"kind": "otp_6", "owner": owner, "expiry_seconds": expiry_seconds}
    async with Client(mcp_params(world)) as client:
        result = await client.call_tool("generate_token", args)
    assert not result.is_error, result
    data = result.structured_content
    return data["value"], data["metadata"]


def verify(world, value, owner=OWNER):
    return world.http.post("/verify", headers=auth(world.keys["billing-agent"]), json={"owner": owner, "value": value})


def wrong_for(code):
    return next(c for c in ("000000", "111111") if c != code)


async def test_redeem_cross_process(world):
    """AC1: issued by the MCP server process, redeemed through the HTTP service process."""
    code, _ = await issue(world)
    r = verify(world, code)
    assert (r.status_code, r.json()) == (200, {"outcome": "redeemed"})
    assert rows(world.db, "SELECT outcome FROM redemption_outcomes") == [("redeemed",)]


async def test_redeem_twice(world):
    """AC2: the second redemption is a 409 already_used."""
    code, _ = await issue(world)
    assert verify(world, code).status_code == 200
    again = verify(world, code)
    assert (again.status_code, again.json()["error"]) == (409, "already_used")


async def test_redeem_expired(world):
    """AC3: after expiry the code is a 410 expired. AC4: a wrong code is a 404 unknown."""
    code, _ = await issue(world, expiry_seconds=5)
    await asyncio.sleep(6)
    r = verify(world, code)
    assert (r.status_code, r.json()["error"]) == (410, "expired")
    wrong = verify(world, wrong_for(code))
    assert (wrong.status_code, wrong.json()["error"]) == (404, "unknown")


async def test_redeem_rate_limited(world):
    """AC5: five wrong tries lock the owner out, even for the correct code."""
    code, _ = await issue(world)
    assert [verify(world, wrong_for(code)).status_code for _ in range(5)] == [404] * 5
    r = verify(world, code)
    assert (r.status_code, r.json()["error"]) == (429, "rate_limited")
    assert rows(world.db, "SELECT used_at IS NULL FROM tokens WHERE owner = %s", OWNER) == [(True,)]


async def test_trace_real_rows(world):
    """AC6: the trace of a redeemed code shows client, tool, batch and token, with no code or hash."""
    code, meta = await issue(world)
    assert verify(world, code).status_code == 200
    r = world.http.get(f"/admin/issuances/{meta['reference']}", headers=auth(world.keys["admin"]))
    body = r.json()
    assert r.status_code == 200
    assert body["client"]["name"] == "billing-agent" and body["tool"] == "generate_token"
    assert body["bytesConsumed"] == meta["bytes_consumed"] > 0
    assert [(b["batchId"], b["source"]) for b in body["batches"]] == [(meta["batch_id"], "qrypt")]
    assert isinstance(body["batches"][0]["byteOffset"], int)
    token = body["token"]
    assert (token["owner"], token["kind"]) == (OWNER, "otp_6") and token["usedAt"]
    stored = rows(world.db, "SELECT encode(token_hash, 'hex') FROM tokens WHERE owner = %s", OWNER)[0][0]
    assert stored not in r.text and "hash" not in r.text and '"value"' not in r.text


async def test_trace_errors_real(world):
    """AC7: an unknown reference is a 404 and the client key is a 403, with the real credentials."""
    _, meta = await issue(world)
    unknown = world.http.get(f"/admin/issuances/{uuid.uuid4()}", headers=auth(world.keys["admin"]))
    assert (unknown.status_code, unknown.json()["error"]) == (404, "not_found")
    other = world.http.get(f"/admin/issuances/{meta['reference']}", headers=auth(world.keys["billing-agent"]))
    assert (other.status_code, other.json()["error"]) == (403, "forbidden")


async def test_roles_across_processes(world):
    """AC8: the issuing role cannot read tokens, the HTTP role cannot update an issuance, on the same database."""
    await issue(world)
    with psycopg.connect(world.db, autocommit=True) as conn:
        for role in ("mcp_server", "http_service"):
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal("s33-test-only"))
            )

    def as_role(role, statement):
        url = make_url(world.db).set(username=role, password="s33-test-only").render_as_string(hide_password=False)  # noqa: S106
        with psycopg.connect(url, autocommit=True) as conn:
            return conn.execute(statement).rowcount

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role("mcp_server", "SELECT * FROM tokens")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role("http_service", "UPDATE issuances SET bytes_consumed = 0")


async def test_report_counts_real_attempts(world):
    """AC9: the success and redeemed panels count this run's outcome rows and tokens (not seed tokens)."""
    code, _ = await issue(world)
    await issue(world, owner="user-43")  # never redeemed
    assert verify(world, code).status_code == 200
    assert verify(world, wrong_for(code), owner="user-43").status_code == 404
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    panels = done.stdout
    assert re.search(r"PANEL 1 .*\n\s+\d{4}-\d\d-\d\d: 1 of 2", panels), panels
    assert re.search(r"PANEL 3 .*\n\s+\d{4}-\d\d-\d\d: 1 of 2", panels), panels
