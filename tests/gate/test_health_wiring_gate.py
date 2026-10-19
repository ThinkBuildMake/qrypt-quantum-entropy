"""S35 gate cases: health and metrics wired across two processes through the database (docs/stories/S35-w-health.md).

Real Postgres, a uvicorn subprocess for the HTTP service, the real stdio MCP server as a subprocess and the mock
Qrypt. Nothing stands in for a module. Timing is real (the report is every 5 s, "not reporting" after 15 s).
"""

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from mcp.client import Client

from tests.conftest import server_env
from tests.mock_qrypt import MockQrypt
from tests.test_mcp_connect import launch

HEX = {"length": 16, "encoding": "hex"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def http_service(url):
    port = free_port()
    env = {"PATH": os.environ["PATH"], **server_env(), "DATABASE_URL": url}
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                httpx.get(f"{base}/health")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
async def stack(db, monkeypatch, tmp_path):
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        seeded = subprocess.run(
            [sys.executable, "scripts/seed.py"], env=os.environ.copy(), capture_output=True, text=True, check=True
        ).stdout
        keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded))
        with http_service(db) as base:
            yield SimpleNamespace(db=db, mock=mock, keys=keys, base=base, tmp=tmp_path)


def health(stack) -> dict:
    return httpx.get(f"{stack.base}/health").json()


def metrics(stack, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx.get(f"{stack.base}/metrics", headers=headers)


def sample(text: str, name: str, **labels) -> float | None:
    for line in text.splitlines():
        m = re.fullmatch(r"(\w+)(?:\{(.*)\})? (\S+)", line)
        if m and m[1] == name:
            have = dict(re.findall(r'(\w+)="([^"]*)"', m[2] or ""))
            if all(have.get(k) == v for k, v in labels.items()):
                return float(m[3])
    return None


async def test_health_empty_before_any_server(stack):
    """AC1: no server ever started: 200, reporting false."""
    r = httpx.get(f"{stack.base}/health")
    assert r.status_code == 200 and r.json()["reporting"] is False


async def test_health_real_report_not_reporting_and_restart(stack):
    """AC2, AC3, AC7 and the restart: a running server reports; stopped, it reads as not reporting."""
    async with Client(launch(stack.tmp, stack.keys["billing-agent"])) as client:
        await client.call_tool("get_random_bytes", HEX)
        await asyncio.sleep(10)
        h = health(stack)
        assert h["reporting"] is True and h["reportAgeSeconds"] <= 6
        assert (h["pool"]["lowMarkBytes"], h["pool"]["highMarkBytes"]) == (32768, 131072)
        assert h["pool"]["levelBytes"] > 0 and h["upstream"]["status"] == "ok"
        assert sample(metrics(stack, stack.keys["admin"]).text, "qrypt_pool_level_bytes") > 0
        assert sample(metrics(stack, stack.keys["admin"]).text, "qrypt_calls_total", tool="get_random_bytes") >= 1
    await asyncio.sleep(16)
    assert health(stack)["reporting"] is False
    text = metrics(stack, stack.keys["admin"]).text  # AC7: only the report age (and the budget) remain
    assert sample(text, "qrypt_report_age_seconds") > 15
    assert (
        sample(text, "qrypt_pool_level_bytes") is None
        and sample(text, "qrypt_calls_total", tool="get_random_bytes") is None
    )
    async with Client(launch(stack.tmp, stack.keys["billing-agent"])) as client:
        await client.list_tools()
        await asyncio.sleep(1)
    with psycopg.connect(stack.db) as conn:
        assert conn.execute("SELECT count(DISTINCT instance_id) FROM server_status").fetchone() == (2,)


async def test_health_misconfigured(stack):
    """AC4: the mock refuses the token: upstream misconfigured and the log names the invalid or expired token."""
    stack.mock.token = "some-other-token"  # the server's QRYPT_API_KEY no longer matches
    async with Client(launch(stack.tmp, stack.keys["billing-agent"])):
        await asyncio.sleep(7)
        h = health(stack)
    assert h["reporting"] is True and h["upstream"]["status"] == "misconfigured"
    events = [json.loads(line) for line in (stack.tmp / "server.log").read_text().splitlines()]
    failed = [e for e in events if e["event"] == "pool_startup_check_failed"]
    assert failed and "token" in failed[0]["cause"].lower() and "expired" in failed[0]["cause"].lower()


async def test_metrics_real_values(stack):
    """AC5, AC6: the operating series match real calls; the route needs an admin key."""
    async with Client(launch(stack.tmp, stack.keys["billing-agent"])) as client:
        for _ in range(5):
            assert not (await client.call_tool("get_random_bytes", HEX)).is_error
        assert (await client.call_tool("get_random_bytes", {"length": 0, "encoding": "hex"})).is_error
        await asyncio.sleep(7)
        r = metrics(stack, stack.keys["admin"])
    assert r.status_code == 200
    t = r.text
    assert sample(t, "qrypt_errors_total", code="invalid_argument") == 1
    assert sample(t, "qrypt_calls_total", tool="get_random_bytes") == 5
    for series in ("qrypt_pool_level_bytes", "qrypt_upstream_requests_last_10s", "qrypt_budget_used_kib"):
        assert sample(t, series) is not None
    assert sample(t, "qrypt_tool_call_duration_ms", quantile="0.95") is not None
    assert metrics(stack, stack.keys["billing-agent"]).status_code == 403
    assert metrics(stack).status_code == 401
