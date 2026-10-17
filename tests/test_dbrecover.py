"""S04 gate cases: the connection layer survives a database restart and fails plainly while it is down."""

import getpass
import json
import os
import shutil
import subprocess
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.engine import make_url

from qrypt_entropy.errors import TemporarilyUnavailable
from qrypt_entropy.service import access, issuance, meter
from qrypt_entropy.storage import db
from qrypt_entropy.transport import http_app, mcp_server

DEAD_URL = "postgresql://dev:dev@127.0.0.1:1/none"  # nothing listens on port 1: the database is "stopped"


@pytest.fixture(autouse=True)
def _close():
    yield
    db.close_pools()


def restart_server() -> None:
    subprocess.run(["service", "postgresql", "restart"], check=True, capture_output=True)


@pytest.fixture
def restartable(empty_db):
    """Skip unless opted in (ALLOW_DB_RESTART=1, `make test-db-restart`) and the local Postgres can be restarted."""
    if os.environ.get("ALLOW_DB_RESTART") != "1":
        pytest.skip(
            "real server restart is opt-in: set ALLOW_DB_RESTART=1 (make test-db-restart); it disrupts other users"
        )
    host = make_url(empty_db).host or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"cannot restart a database on {host}: only a local server can be controlled")
    if shutil.which("service") is None or getpass.getuser() != "root":
        pytest.skip("cannot control the server: needs `service` and root to run `service postgresql restart`")
    if subprocess.run(["service", "postgresql", "status"], capture_output=True).returncode != 0:
        pytest.skip("`service postgresql status` is not online: the local server is not managed by the service command")
    return empty_db


def read_within(url: str, seconds: float = 30.0):
    deadline = time.monotonic() + seconds
    while True:
        try:
            with db.connection(url) as conn:
                return conn.execute("SELECT 1").fetchone()[0]
        except TemporarilyUnavailable:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def postmaster_start(url: str):
    with db.connection(url) as conn:
        return conn.execute("SELECT pg_postmaster_start_time()").fetchone()[0]


def test_reconnect_after_backends_killed(empty_db):  # AC1, non-disruptive: only this database's backends die
    def pid():
        with db.connection(empty_db) as conn:
            return conn.execute("SELECT pg_backend_pid()").fetchone()[0]

    before = pid()
    with psycopg.connect(empty_db, autocommit=True) as killer:
        killer.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
    deadline = time.monotonic() + 30
    while True:
        try:
            after = pid()
            break
        except TemporarilyUnavailable:
            assert time.monotonic() < deadline
            time.sleep(0.3)
    assert after != before


def test_meter_outage():
    with pytest.raises(TemporarilyUnavailable):
        meter.reserve(8, "refill", "qrypt", url=DEAD_URL)


def test_db_restart_reconnect(restartable):  # AC1
    url = restartable
    assert read_within(url) == 1  # the pool now holds an open connection
    started = postmaster_start(url)
    restart_server()
    assert read_within(url, 30) == 1  # same process, same pool
    assert postmaster_start(url) != started  # the server really restarted


def test_acknowledged_writes_survive(restartable):  # AC4
    url = restartable
    with db.connection(url) as conn:
        conn.execute("CREATE TABLE ack (n int PRIMARY KEY)")
    for n in range(100):
        with db.connection(url) as conn:  # the commit on exit is the acknowledgement
            conn.execute("INSERT INTO ack VALUES (%s)", (n,))
    restart_server()
    deadline = time.monotonic() + 30
    while True:
        try:
            with psycopg.connect(url) as conn:
                assert conn.execute("SELECT count(*) FROM ack").fetchone()[0] == 100
            break
        except psycopg.OperationalError:
            assert time.monotonic() < deadline
            time.sleep(0.3)


def test_unreachable_database_raises_temporarily_unavailable():
    started = time.monotonic()
    with pytest.raises(TemporarilyUnavailable) as err:
        with db.connection(DEAD_URL):
            raise AssertionError("no connection should be handed out")
    assert err.value.http_status == 503
    assert time.monotonic() - started < db.POOL_WAIT_SECONDS + 3


def test_http_db_down(monkeypatch):  # AC2
    def read_clients():
        with db.connection(DEAD_URL) as conn:
            return conn.execute("SELECT 1").fetchone()

    monkeypatch.setattr(http_app.meter, "get_usage", read_clients)
    monkeypatch.setattr(access, "authenticate", lambda key, roles: access.Principal("c1", "admin", "n"))
    resp = TestClient(http_app.app).get("/admin/usage", headers={"Authorization": "Bearer k"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "temporarily_unavailable"


async def test_tool_db_down(monkeypatch):  # AC3
    async def get_random_bytes(principal, length, encoding):
        with db.connection(DEAD_URL) as conn:
            conn.execute("SELECT 1")
        return "value-that-must-not-leak"

    monkeypatch.setattr(issuance, "get_random_bytes", get_random_bytes)
    server = mcp_server.build_server(lambda: "stub-client-key")
    with pytest.raises(ToolError) as err:
        await server.call_tool("get_random_bytes", {"length": 8, "encoding": "hex"})
    body = json.loads(str(err.value))
    assert body["error"] == "temporarily_unavailable"
    assert "value-that-must-not-leak" not in str(err.value)
