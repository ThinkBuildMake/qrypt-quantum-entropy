"""S03 gate cases: logs, error format, configuration, UTC (docs/stories/S03-logcfg.md AC1-AC9)."""

import base64
import json
import logging
import os
import subprocess
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client
from sqlalchemy.engine import make_url

from qrypt_entropy import logs
from qrypt_entropy.config import Settings
from qrypt_entropy.errors import HTTP_STATUS, AppError
from qrypt_entropy.storage.db import open_pool
from qrypt_entropy.transport.http_app import app
from qrypt_entropy.transport.mcp_server import build_server
from tests.conftest import server_env
from tools import check_env

CLIENT_KEY = "stub-client-key"
CLIENT_ID = "00000000-0000-4000-8000-0000000000c1"
BASE_ENV = {"PATH": os.environ["PATH"], "LOG_FILE": "", **server_env()}


def run(args, env):
    return subprocess.run([sys.executable, *args], env={**BASE_ENV, **env}, capture_output=True, text=True, check=False)


def mcp_raw(env=None):
    """Start the stub MCP server, list its tools and call one; stdout and stderr stay separate."""
    return run(["scripts/mcp_client.py", "raw"], {"MCP_CLIENT_KEY": CLIENT_KEY, **(env or {})})


def http_session():
    code = (
        "from fastapi.testclient import TestClient\n"
        "from qrypt_entropy.transport.http_app import app\n"
        "c = TestClient(app)\n"
        f"c.post('/verify', headers={{'Authorization': 'Bearer {CLIENT_KEY}'}}, json={{'owner': 'o', 'value': '1'}})\n"
        "c.get('/health')\n"
        "from qrypt_entropy.service.access import close_pools\nclose_pools()\n"
    )
    return run(["-c", code], {})


def lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_logs_are_json():
    http, mcp = http_session(), mcp_raw()
    assert http.returncode == 0 and mcp.returncode == 0, (http.stderr[-300:], mcp.stderr[-300:])
    for out in (http.stderr, mcp.stderr):
        parsed = lines(out)  # a non-JSON line raises here
        assert len(parsed) >= 2
        assert all({"timestamp", "level", "event"} <= set(line) for line in parsed)


def test_logs_carry_client_id():
    http, mcp = http_session(), mcp_raw()
    for out in (http.stderr, mcp.stderr):
        with_id = [line for line in lines(out) if line.get("client_id")]
        assert with_id and all(line["client_id"] == CLIENT_ID for line in with_id)
        assert CLIENT_KEY not in out


def test_mcp_stdout_is_protocol_only():
    result = mcp_raw()
    out = lines(result.stdout)
    assert len(out) == 3
    assert all(m["jsonrpc"] == "2.0" for m in out)
    assert all(line["event"] for line in lines(result.stderr))


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "out.log"
    logs.configure_logging("INFO", str(path))
    yield path
    logs.configure_logging()


def test_logs_scrub_markers(log_path):
    secret, value = "MARKER-secret-8f3a9c1d", "MARKER-value-51b7e2a0"
    logs.register_secret(secret)
    logs.register_secret(value)
    logs.get_logger("t").info(
        "probe",
        value=value,
        key=secret,
        pepper=secret,
        token=secret,
        bytes=value.encode(),
        note=f"inside {secret} and {value.encode().hex()} and {base64.b64encode(value.encode()).decode()}",
    )
    text = log_path.read_text()
    assert "[redacted]" in text
    assert secret not in text and value not in text and value.encode().hex() not in text
    assert base64.b64encode(value.encode()).decode() not in text
    assert json.loads(text)["event"] == "probe"


@pytest.fixture
def raising_routes():
    """Test routes that raise each code, added to the real app (so its handlers apply) and removed afterwards."""
    before = list(app.router.routes)
    for code in HTTP_STATUS:
        app.add_api_route(f"/__test/raise/{code}", lambda code=code: _raise(code))
    yield
    app.router.routes[:] = before


def _raise(code):
    raise AppError("refused", code)


@pytest.mark.parametrize("code", sorted(HTTP_STATUS))
async def test_error_codes_http_and_tool(code, raising_routes):
    r = TestClient(app).get(f"/__test/raise/{code}")
    assert r.json() == {"error": code, "message": "refused"}
    assert r.status_code == HTTP_STATUS[code]
    server = build_server(lambda: CLIENT_KEY)
    server.tool(name="raises")(lambda: _raise(code))
    async with Client(server) as client:
        result = await client.call_tool("raises", {})
    assert result.is_error
    assert json.loads(result.content[0].text) == {"error": code, "message": "refused"}


def test_error_codes_cover_the_architecture_list():
    assert set(HTTP_STATUS) >= {
        "invalid_argument", "unauthorized", "forbidden", "not_found",
        "budget_exhausted", "temporarily_unavailable", "upstream_misconfigured", "database_unavailable",
    }  # fmt: skip


PROFILES = [("dev", "DEBUG", "logs/server.log"), ("staging", "INFO", ""), ("prod", "INFO", "")]


@pytest.mark.parametrize(("profile", "level", "log_file"), PROFILES)
def test_profiles_set_their_own_defaults(monkeypatch, profile, level, log_file):
    for name in ("LOG_LEVEL", "LOG_FILE", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_PROFILE", profile)
    s = Settings(_env_file=None)
    assert (s.app_env, s.log_level, s.log_file) == (profile, level, log_file)


def test_profile_does_not_override_explicit_settings(monkeypatch):
    monkeypatch.setenv("APP_PROFILE", "prod")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert Settings(_env_file=None).log_level == "ERROR"


@pytest.mark.parametrize("profile", ["dev", "staging", "prod"])
def test_config_show_prints_profile_without_secrets(profile):
    secrets = {"PEPPER": "MARKER-pepper-77ac", "QRYPT_API_TOKEN": "MARKER-token-1f"}
    result = run(["scripts/config_show.py"], {"APP_PROFILE": profile, **secrets})
    assert f"app_env={profile}" in result.stdout.splitlines()
    assert "MARKER" not in result.stdout + result.stderr


def test_missing_pepper_stops_start_naming_variable():
    env = {"MCP_CLIENT_KEY": "MARKER-launch-key-31"}
    result = run(["-m", "qrypt_entropy.transport.mcp_server"], {**env, "PEPPER": ""})
    assert result.returncode == 1
    assert "PEPPER" in result.stderr
    assert "MARKER" not in result.stdout + result.stderr
    assert result.stdout == ""


def test_env_example_lists_every_setting_with_placeholders():
    assert check_env.problems() == []


def test_check_env_flags_missing_and_secret_values():
    text = "APP_ENV=dev\nPEPPER=hunter2hunter2hunter2hunter2\nDATABASE_URL=postgresql://admin:s3cretvalue@h/db\n"
    found = " ".join(check_env.problems(text))
    assert "LOG_LEVEL" in found and "PEPPER" in found and "DATABASE_URL" in found


def test_times_are_utc(empty_db):
    with psycopg.connect(empty_db, autocommit=True) as admin:
        name = make_url(empty_db).database
        admin.execute(f"ALTER DATABASE \"{name}\" SET timezone TO 'America/Chicago'")
    with psycopg.connect(empty_db) as raw:
        assert raw.execute("SELECT now()::text").fetchone()[0].endswith(("-05", "-06"))  # the server zone is real
    pool = open_pool(empty_db)
    try:
        with pool.connection() as conn:
            stamp = conn.execute("SELECT now()::text").fetchone()[0]
    finally:
        pool.close()
    assert stamp.endswith("+00")


@pytest.mark.parametrize("name", ["PEPPER", "TOKEN_PEPPER"])
def test_pepper_is_one_setting_with_two_names(monkeypatch, name):
    for var in ("PEPPER", "TOKEN_PEPPER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(name, "alias-check")
    assert Settings(_env_file=None).token_pepper.get_secret_value() == "alias-check"


@pytest.fixture
def probe(log_path):
    secret = "MARKER-nested-example"
    logs.register_secret(secret)
    return secret, log_path


def test_logs_scrub_nested_exception_positional_and_library_lines(probe):
    secret, path = probe
    logger = logs.get_logger("t")
    logger.info("nested", detail={"inner": [secret, {"deep": secret}]}, items=[secret], err=ValueError(secret))
    logger.info("positional %s", secret)
    logging.getLogger("library").info('{"event": "%s"}', secret)
    logging.getLogger("library").info("plain %s", secret.encode().hex().upper())
    text = path.read_text()
    assert len(text.splitlines()) == 4 and secret not in text
    assert secret.encode().hex().upper() not in text
    assert all(json.loads(line) for line in text.splitlines())


def test_short_markers_leave_ordinary_words_alone(log_path):
    logs.register_secret("tem")
    logs.get_logger("t").error("temporarily_unavailable", level_note="x")
    assert json.loads(log_path.read_text())["event"] == "temporarily_unavailable"


def test_sensitive_names_match_by_word_but_counts_pass(log_path):
    logs.get_logger("t").info("probe", api_key="abc", x_api_key="abc", client_key="abc", bytes_consumed=8, monkey="ok")
    line = json.loads(log_path.read_text())
    assert [line[k] for k in ("api_key", "x_api_key", "client_key")] == ["[redacted]"] * 3
    assert (line["bytes_consumed"], line["monkey"]) == (8, "ok")


def test_http_service_refuses_to_start_without_its_secrets(monkeypatch):
    from qrypt_entropy.config import ConfigError, get_settings

    monkeypatch.setenv("KEY_HASH_SECRET", "")
    monkeypatch.setenv("PEPPER", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="PEPPER, KEY_HASH_SECRET"), TestClient(app):
            pass
    finally:
        get_settings.cache_clear()
