"""Shared fixtures. The suite is offline: it needs only a Postgres reachable at DATABASE_URL (`make db-up`)."""

import os
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access
from qrypt_entropy.service.access import STUB_ADMIN_KEY, STUB_CLIENT_KEY, close_pools, hash_key
from tests.test_migrations import migrate

os.environ.setdefault("KEY_HASH_SECRET", "test-only-key-hash-secret")  # not a real secret

GATE_STATS: dict[str, str] = {}


def base_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5432/postgres")


def url_for(dbname: str) -> str:
    return make_url(base_url()).set(database=dbname).render_as_string(hide_password=False)


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


@pytest.fixture(scope="session", autouse=True)
def stub_credentials():
    """Give the two fixed test keys real credential rows in the gate database, so the stubbed flows authenticate."""
    rows = [
        ("00000000-0000-4000-8000-0000000000c1", "stub-client", "client", STUB_CLIENT_KEY),
        ("00000000-0000-4000-8000-0000000000a1", "stub-admin", "admin", STUB_ADMIN_KEY),
    ]
    try:
        with psycopg.connect(base_url()) as conn:
            for cid, name, role, key in rows:
                conn.execute(
                    "INSERT INTO credentials (id, name, role, key_hash) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET key_hash = EXCLUDED.key_hash",
                    (cid, name, role, hash_key(key)),
                )
    except psycopg.Error:
        pass  # no migrated database: only tests that need one will fail
    yield
    close_pools()


@pytest.fixture
def stdio_env():
    return {"PATH": os.environ["PATH"], "MCP_CLIENT_KEY": "stub-client-key", "LOG_FILE": "", **server_env()}


def server_env() -> dict[str, str]:
    """What a launched MCP server process needs besides its client key."""
    keys = ("DATABASE_URL", "KEY_HASH_SECRET")
    return {"PEPPER": "test-pepper", **{k: os.environ[k] for k in keys if k in os.environ}}


def pytest_terminal_summary(terminalreporter):
    if GATE_STATS:
        terminalreporter.section("gate harness")
        for key, value in GATE_STATS.items():
            terminalreporter.write_line(f"{key}: {value}")


PYTHON = sys.executable
