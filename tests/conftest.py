"""Shared fixtures. The suite is offline: it needs only a Postgres reachable at DATABASE_URL (`make db-up`)."""

import asyncio
import json
import os
import sys
import threading
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from qrypt_entropy import logs
from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import AppError
from qrypt_entropy.service import access, meter, pool
from qrypt_entropy.service.access import close_pools
from qrypt_entropy.storage.db import close_pools as close_db_pools
from qrypt_entropy.upstream import source
from tests.mock_qrypt import MockQrypt
from tests.test_migrations import migrate

os.environ.setdefault("KEY_HASH_SECRET", "test-only-key-hash-secret")  # not a real secret
os.environ.setdefault("TOKEN_PEPPER", "test-only-token-pepper")  # not a real secret
os.environ.pop("PEPPER", None)  # a shell that ran the README quick-start exports these; tests set their own
os.environ.pop("MCP_CLIENT_KEY", None)


def _start_session_mock() -> MockQrypt:
    """One mock Qrypt for the whole run, in its own thread, so servers launched as processes can reach it too.
    The suite never talks to the live service: QRYPT_BASE_URL and QRYPT_API_KEY are always the mock's."""
    mock, ready = MockQrypt(token="session-mock-token", enforce_limit=False), threading.Event()  # noqa: S106

    async def serve() -> None:
        async with mock:
            ready.set()
            await asyncio.Event().wait()

    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    assert ready.wait(10)
    return mock


SESSION_MOCK = _start_session_mock()
os.environ["QRYPT_BASE_URL"] = SESSION_MOCK.url
os.environ["QRYPT_API_KEY"] = SESSION_MOCK.token
os.environ.pop("QRYPT_API_TOKEN", None)

GATE_STATS: dict[str, str] = {}

# S38 AC4: every log line any in-process test writes is parsed at the end of the session
LOG_LINES: list[str] = []
_format = logs._LibraryJsonFormatter.format


def _recording_format(self, record):
    line = _format(self, record)
    LOG_LINES.append(line)
    return line


logs._LibraryJsonFormatter.format = _recording_format


def log_line_problems() -> tuple[int, int, int, int]:
    """(lines, lines that are not JSON, tool-call lines, tool-call lines without duration_ms)."""
    not_json = tool_calls = missing = 0
    for line in LOG_LINES:
        try:
            event = json.loads(line)
        except ValueError:
            not_json += 1
            continue
        if str(event.get("event", "")).startswith("tool_call"):
            tool_calls += 1
            missing += "duration_ms" not in event
    return len(LOG_LINES), not_json, tool_calls, missing


def base_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5432/postgres")


def url_for(dbname: str) -> str:
    return make_url(base_url()).set(database=dbname).render_as_string(hide_password=False)


def seed_issuance(ref: str) -> None:
    """An issuance row (by the test client, drawn from a received batch) for tests that trace a fixed reference."""
    batch = "00000000-0000-4000-8000-0000000000b1"
    with psycopg.connect(base_url()) as conn:
        conn.execute(
            "INSERT INTO batches (id, purpose, kib, outcome) VALUES (%s, 'refill', 64, 'received')"
            " ON CONFLICT DO NOTHING",
            (batch,),
        )
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) "
            "VALUES (%s, %s, 'get_random_bytes', 8) ON CONFLICT DO NOTHING",
            (ref, client_id()),
        )
        conn.execute(
            "INSERT INTO issuance_batches (issuance_id, batch_id, byte_offset, byte_count) "
            "VALUES (%s, %s, 0, 8) ON CONFLICT DO NOTHING",
            (ref, batch),
        )


@pytest.fixture
def empty_db():
    """A fresh empty database on the server DATABASE_URL points at; dropped afterwards."""
    name = f"qrypt_test_{uuid.uuid4().hex[:12]}"
    admin = make_url(base_url()).database
    with psycopg.connect(url_for(admin), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield url_for(name)
    finally:
        with psycopg.connect(url_for(admin), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db(empty_db, monkeypatch):
    """A fresh migrated database that Access talks to."""
    assert migrate(empty_db).returncode == 0
    monkeypatch.setenv("DATABASE_URL", empty_db)
    get_settings.cache_clear()
    access.close_pools()
    yield empty_db
    access.close_pools()
    get_settings.cache_clear()


TEST_KEYS: dict[str, str] = {}
TEST_IDS: dict[str, str] = {}


def client_key() -> str:
    return TEST_KEYS["client"]


def admin_key() -> str:
    return TEST_KEYS["admin"]


def client_id() -> str:
    return TEST_IDS["client"]


@pytest.fixture(scope="session", autouse=True)
def test_credentials():
    """Real credentials (made through Access) in the gate database for the tests that talk to it."""
    suffix = uuid.uuid4().hex[:8]
    original_cap = None
    try:
        with psycopg.connect(base_url()) as conn:  # every server start in the suite spends budget, and use never falls
            original_cap = conn.execute("SELECT cap_kib FROM budget").fetchone()[0]
            conn.execute("UPDATE budget SET cap_kib = 1000000000")

        for role in ("client", "admin"):
            principal, TEST_KEYS[role] = access.create_credential(role, f"test-{role}-{suffix}")
            TEST_IDS[role] = principal.client_id
    except AppError, psycopg.Error:
        pass  # no migrated database: only tests that need one will fail
    yield
    close_pools()
    close_db_pools()
    meter.close_pools()
    if original_cap is not None:
        with psycopg.connect(base_url()) as conn:
            conn.execute("UPDATE budget SET cap_kib = %s", (original_cap,))


@pytest.fixture(autouse=True)
async def fresh_process_pool():
    """The Pool and the entropy source are process-wide singletons: no case may inherit another's (S12 review)."""
    pool.use_pool(None)
    source.use_source(None)
    yield
    await pool.shutdown()
    source.use_source(None)


def server_env() -> dict[str, str]:
    """What a launched MCP server process needs besides its client key."""
    keys = ("DATABASE_URL", "KEY_HASH_SECRET", "QRYPT_BASE_URL", "QRYPT_API_KEY")
    return {"PEPPER": "test-pepper", **{k: os.environ[k] for k in keys if k in os.environ}}


def pytest_sessionfinish(session):
    _, not_json, _, missing = log_line_problems()
    if not_json or missing:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    lines, not_json, tool_calls, missing = log_line_problems()
    if lines:
        terminalreporter.section("log lines")
        terminalreporter.write_line(
            f"log lines parsed as JSON: {lines - not_json} of {lines}; "
            f"tool-call lines with duration_ms: {tool_calls - missing} of {tool_calls}"
        )
    if GATE_STATS:
        terminalreporter.section("gate harness")
        for key, value in GATE_STATS.items():
            terminalreporter.write_line(f"{key}: {value}")


PYTHON = sys.executable
