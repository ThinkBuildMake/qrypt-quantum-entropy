"""S34 gate cases: the operate flow joined end to end (docs/stories/S34-w-operate.md).

Real Postgres, the real HTTP service as a uvicorn subprocess, real stdio MCP server processes (or the real server
object where the pool level must be read), the real Access, Meter and Pool, and the committed mock Qrypt. The admin
key and the seed keys come from `scripts/seed.py`, as they do for a person.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import pool
from qrypt_entropy.transport.mcp_server import build_server
from tests.test_mcp_connect import TOOLS, launch, refusal

KIB = 1024
HEX = {"length": 1024, "encoding": "hex"}


def rows(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def body(result):
    return json.loads(result.content[0].text) if result.is_error else result.structured_content


@pytest.fixture
def operate(db, tmp_path):
    """A migrated, seeded database and the real HTTP service (uvicorn) on it. Returns helpers for the admin routes."""
    get_settings.cache_clear()
    seeded = subprocess.run(
        [sys.executable, "scripts/seed.py"], env=os.environ.copy(), capture_output=True, text=True, check=True
    ).stdout
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded))
    port = free_port()
    server = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(port)],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    http = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {keys['admin']}"})
    for _ in range(150):
        try:
            http.get("/health")
            break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        server.kill()
        pytest.fail("the HTTP service did not start")

    def create(name):
        made = http.post("/admin/clients", json={"name": name})
        assert made.status_code == 201
        return made.json()["client"]["id"], made.json()["key"]

    try:
        yield SimpleNamespace(db=db, http=http, keys=keys, tmp=tmp_path, create=create)
    finally:
        http.close()
        server.terminate()
        server.wait(timeout=10)
        get_settings.cache_clear()


def usage(op):
    r = op.http.get("/admin/usage")
    assert r.status_code == 200
    return r.json()


def test_usage_empty(operate):
    """AC1: after db-reset and seed the usage view is empty."""
    assert usage(operate) == {"budget": {"capKib": 10240, "usedKib": 0}, "perClient": []}


async def test_created_client_connects(operate):
    """AC2: a client created through the admin route connects to the real stdio server and lists the tools."""
    _, key = operate.create("reporting-agent")
    async with Client(launch(operate.tmp, key)) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS


async def test_usage_matches_metadata(operate):
    """AC3: perClient bytesConsumed is the sum of bytes_consumed in each client's tool metadata; usedKib is the
    sum of the batches."""
    consumed = {"billing-agent": 0, "reporting-agent": 0}
    _, reporting = operate.create("reporting-agent")
    for name, key in (("billing-agent", operate.keys["billing-agent"]), ("reporting-agent", reporting)):
        async with Client(launch(operate.tmp, key)) as client:
            for args in ({"length": 16, "encoding": "hex"}, {"length": 40, "encoding": "base64"}):
                consumed[name] += body(await client.call_tool("get_random_bytes", args))["metadata"]["bytes_consumed"]
            consumed[name] += body(await client.call_tool("random_int", {"min": 1, "max": 1000}))["metadata"][
                "bytes_consumed"
            ]
    view = usage(operate)
    assert {c["name"]: c["bytesConsumed"] for c in view["perClient"]} == consumed
    assert view["budget"]["usedKib"] == rows(operate.db, "SELECT sum(kib)::int FROM batches")[0][0] > 0


async def test_revoked_cannot_connect(operate):
    """AC4: a client revoked through the admin route is refused with unauthorized, and its connect spends nothing."""
    client_id, key = operate.create("reporting-agent")
    assert operate.http.post(f"/admin/clients/{client_id}/revoke").json()["client"]["status"] == "revoked"
    assert (await refusal(operate.tmp, key))["error"] == "unauthorized"
    assert usage(operate)["budget"]["usedKib"] == 0


async def test_revoked_next_call(operate):
    """AC5: an open session is refused on its next call; the pool level and used_kib do not move."""
    client_id, key = operate.create("late-agent")
    async with Client(build_server(lambda: key)) as client:
        first = body(await client.call_tool("get_random_bytes", HEX))
        assert first["metadata"]["bytes_consumed"] == KIB
        level, used = pool.get_pool().level, usage(operate)["budget"]["usedKib"]
        before = rows(operate.db, "SELECT count(*), sum(bytes_consumed) FROM issuances")
        assert operate.http.post(f"/admin/clients/{client_id}/revoke").status_code == 200
        result = await client.call_tool("get_random_bytes", HEX)
        assert result.is_error and body(result)["error"] == "unauthorized" and "value" not in body(result)
        assert pool.get_pool().level == level and usage(operate)["budget"]["usedKib"] == used
        assert rows(operate.db, "SELECT count(*), sum(bytes_consumed) FROM issuances") == before


async def drain(client, calls):
    """Call get_random_bytes `calls` times; return the results."""
    return [await client.call_tool("get_random_bytes", HEX) for _ in range(calls)]


def budget_alerts(path):
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]
    return [e["threshold_percent"] for e in lines if e.get("event") == "budget_alert"]


async def test_budget_fail_closed(operate):
    """AC6: with the cap at 100 KiB the calls are served until the budget is spent, then budget_exhausted with no
    value; used_kib never passes 100 and the 80% and 100% alerts are each logged once."""
    _, key = operate.create("reporting-agent")
    assert operate.http.put("/admin/budget", json={"capKib": 100}).json() == {"budget": {"capKib": 100, "usedKib": 0}}
    async with Client(launch(operate.tmp, key)) as client:
        results = await drain(client, 105)
        assert usage(operate)["budget"]["usedKib"] <= 100
    served = [r for r in results if not r.is_error]
    refused = [r for r in results if r.is_error]
    assert 90 <= len(served) <= 100 and len(refused) == 105 - len(served)  # the 1 KiB startup check is served too
    assert all(body(r)["error"] == "budget_exhausted" and "value" not in body(r) for r in refused)
    assert [r.is_error for r in results] == sorted(r.is_error for r in results)  # refusals only after the last success
    assert usage(operate)["budget"] == {"capKib": 100, "usedKib": 100}
    assert max(r[0] for r in rows(operate.db, "SELECT used_kib FROM budget")) <= 100
    assert budget_alerts(operate.tmp / "server.log") == [80, 100]


async def test_budget_lowered_below_use(operate):
    """A cap lowered below what is already used fails closed: no more entropy is bought, calls refuse once the pool
    is spent, used_kib does not rise."""
    _, key = operate.create("reporting-agent")
    async with Client(launch(operate.tmp, key)) as client:
        assert not (await client.call_tool("get_random_bytes", HEX)).is_error
        used = usage(operate)["budget"]["usedKib"]
        assert used >= 65
        assert operate.http.put("/admin/budget", json={"capKib": 10}).status_code == 200
        results = await drain(client, used + 5)
    assert any(r.is_error and body(r)["error"] == "budget_exhausted" for r in results)
    assert usage(operate)["budget"] == {"capKib": 10, "usedKib": used}
    raised = operate.http.put("/admin/budget", json={"capKib": 10240})
    assert raised.json()["budget"]["usedKib"] == used


async def test_budget_alerts_real(operate):
    """The 80% and 100% events come from the real reserve path when the budget is spent through the real server."""
    _, key = operate.create("reporting-agent")
    operate.http.put("/admin/budget", json={"capKib": 100})
    async with Client(launch(operate.tmp, key)) as client:
        await drain(client, 110)
    assert budget_alerts(operate.tmp / "server.log").count(80) == 1
    assert budget_alerts(operate.tmp / "server.log").count(100) == 1


async def test_report_shows_cap_use(operate):
    """AC7: `make report` marks the 80% level against the real reading."""
    _, key = operate.create("reporting-agent")
    operate.http.put("/admin/budget", json={"capKib": 100})
    async with Client(launch(operate.tmp, key)) as client:
        await drain(client, 105)
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    panel = next(line for line in done.stdout.splitlines() if "Qrypt use against the cap" in line)
    assert "100 of 100 KiB (100%), at or above the 80 KiB alert level" in panel
