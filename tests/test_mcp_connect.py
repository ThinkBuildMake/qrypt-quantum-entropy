"""S06: a stdio client connects only with an active client key (AC1-AC5), by really launching the server."""

import json
import os
import subprocess
import sys

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError

from qrypt_entropy.service import access
from tests.conftest import server_env

TOOLS = {"get_random_bytes", "random_int", "generate_token"}


def launch(tmp_path, key, errlog=None):
    env = {"PATH": os.environ["PATH"], "LOG_FILE": str(tmp_path / "server.log"), **server_env()}
    if key is not None:
        env["MCP_CLIENT_KEY"] = key
    return StdioServerParameters(command=sys.executable, args=["-m", "qrypt_entropy.transport.mcp_server"], env=env)


async def refusal(tmp_path, key):
    """Connect with `key`: initialize must fail, and the server must have logged why (last stderr line)."""
    params = launch(tmp_path, key)
    with pytest.raises((MCPError, ExceptionGroup)):
        async with Client(params) as client:
            await client.list_tools()
            pytest.fail("a tool list was returned")
    done = subprocess.run(
        [params.command, *params.args], env=params.env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    assert done.returncode == 1 and done.stdout == ""
    event = json.loads(done.stderr.strip().splitlines()[-1])
    assert event["event"] == "connect_refused"
    return event


async def test_ac1_active_client_key_sees_the_tools(db, tmp_path):
    _, key = access.create_credential("client", "agent")
    async with Client(launch(tmp_path, key)) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS


async def test_ac2_missing_key_is_refused_naming_the_variable(db, tmp_path):
    body = await refusal(tmp_path, None)
    assert body["error"] == "unauthorized"
    assert "MCP_CLIENT_KEY" in body["cause"]


async def test_ac3_unknown_key_is_refused(db, tmp_path):
    body = await refusal(tmp_path, "nope")
    assert body["error"] == "unauthorized"
    assert "nope" not in json.dumps(body)


async def test_ac4_revoked_key_is_refused(db, tmp_path):
    principal, key = access.create_credential("client", "gone")
    access.revoke_client(principal.client_id)
    assert (await refusal(tmp_path, key))["error"] == "unauthorized"


async def test_ac5_admin_key_is_refused_with_forbidden(db, tmp_path):
    _, key = access.create_credential("admin", "root")
    assert (await refusal(tmp_path, key))["error"] == "forbidden"
