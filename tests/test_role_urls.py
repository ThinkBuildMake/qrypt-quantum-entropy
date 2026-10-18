"""H01: the hosted HTTP service and the MCP server connect as their own database roles, never as the owner."""

import re
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from qrypt_entropy.config import ConfigError, Settings, get_settings
from qrypt_entropy.service import access, meter
from qrypt_entropy.storage import db as storage
from qrypt_entropy.transport.http_app import app
from tests.conftest import role_url_for
from tests.test_migrations import migrate

RENDER = (Path(__file__).resolve().parents[1] / "render.yaml").read_text()


def render_env() -> dict[str, str]:
    """env var name -> the lines of its render.yaml entry."""
    blocks = re.split(r"^\s*- key: ", RENDER.split("envVars:")[1].split("databases:")[0], flags=re.M)[1:]
    return {b.split("\n")[0].strip(): b for b in blocks}


def test_render_wires_each_process_to_its_own_role():
    env = render_env()
    assert "DATABASE_URL" not in env, "the owner string must not be the app's DATABASE_URL"
    assert "sync: false" in env["HTTP_SERVICE_URL"]  # the role's string carries its password: set in the dashboard
    assert "fromDatabase" in env["MIGRATOR_URL"]  # only the migration gets the owner string
    assert [k for k, v in env.items() if "connectionString" in v] == ["MIGRATOR_URL"]


def test_dev_falls_back_to_database_url_and_prod_refuses():
    dev = Settings(app_env="dev", database_url="postgresql://owner@h/db")
    dev.bind_role("http_service")
    assert dev.database_url == "postgresql://owner@h/db"
    prod = Settings(app_env="prod", database_url="postgresql://owner@h/db")
    with pytest.raises(ConfigError, match="HTTP_SERVICE_URL"):
        prod.bind_role("http_service")
    with pytest.raises(ConfigError, match="MCP_SERVER_URL"):
        prod.bind_role("mcp_server")
    prod = Settings(app_env="prod", database_url="postgresql://owner@h/db", mcp_server_url="postgresql://m@h/db")
    prod.bind_role("mcp_server")
    assert prod.database_url == "postgresql://m@h/db"


def test_role_urls_are_registered_as_secrets():
    settings = Settings(http_service_url="postgresql://u:pw-http@h/db", migrator_url="postgresql://u:pw-own@h/db")
    assert {"postgresql://u:pw-http@h/db", "postgresql://u:pw-own@h/db"} <= set(settings.secret_values())


def test_hosted_routes_work_as_the_http_service_role(empty_db, monkeypatch):
    assert migrate(empty_db).returncode == 0
    with psycopg.connect(empty_db, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE http_service PASSWORD 'http-test-only'"))
    monkeypatch.setenv("DATABASE_URL", empty_db)
    get_settings.cache_clear()
    _, key = access.create_credential("admin", "root")  # as the owner, like `make seed`
    access.close_pools()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("PEPPER", "test-pepper")
    monkeypatch.setenv("HTTP_SERVICE_URL", role_url_for(empty_db, "http_service", "http-test-only"))
    get_settings.cache_clear()
    headers = {"Authorization": f"Bearer {key}"}
    try:
        with TestClient(app, raise_server_exceptions=False) as http:
            assert http.get("/admin/usage", headers=headers).status_code == 200
            made = http.post("/admin/clients", headers=headers, json={"name": "c1"})
            assert made.status_code == 201
            assert http.put("/admin/budget", headers=headers, json={"capKib": 20480}).status_code == 200
            revoke = http.post(f"/admin/clients/{made.json()['client']['id']}/revoke", headers=headers)
            assert revoke.status_code == 200
            assert http.post("/verify", headers=headers, json={"owner": "u", "value": "123456"}).status_code == 404
            assert http.get("/health").status_code == 200
        user = get_settings().database_url
        assert user.startswith("postgresql://http_service:")
        with psycopg.connect(user) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (gen_random_uuid(), "
                             "gen_random_uuid(), 'x', 1)")  # fmt: skip
    finally:
        access.close_pools()
        storage.close_pools()
        meter.close_pools()
        get_settings.cache_clear()


def test_openapi_and_docs_routes_are_off():
    with TestClient(app) as http:
        assert [http.get(p).status_code for p in ("/openapi.json", "/docs", "/redoc")] == [404, 404, 404]
