"""Shared fixtures. The suite is offline: it needs only a Postgres reachable at DATABASE_URL (`make db-up`)."""

import os
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

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
def stdio_env():
    return {"PATH": os.environ["PATH"], "MCP_CLIENT_KEY": "stub-client-key", "LOG_FILE": ""}


def pytest_terminal_summary(terminalreporter):
    if GATE_STATS:
        terminalreporter.section("gate harness")
        for key, value in GATE_STATS.items():
            terminalreporter.write_line(f"{key}: {value}")


PYTHON = sys.executable
