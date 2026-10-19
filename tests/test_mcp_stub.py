import json
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from tests.conftest import client_key, server_env

TOOLS = {"get_random_bytes", "random_int", "generate_token"}


def params(tmp_path):
    env = {"MCP_CLIENT_KEY": client_key(), "LOG_FILE": str(tmp_path / "server.log"), **server_env()}
    return StdioServerParameters(command=sys.executable, args=["-m", "qrypt_entropy.transport.mcp_server"], env=env)


async def test_ac1_server_lists_three_tools_and_saves_log(tmp_path):
    async with Client(params(tmp_path)) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    fields = {t.name: set(t.input_schema["properties"]) for t in tools}
    assert fields["get_random_bytes"] == {"length", "encoding"}
    assert fields["random_int"] == {"min", "max"}
    assert fields["generate_token"] == {"kind", "owner", "expiry_seconds"}
    first = json.loads((tmp_path / "server.log").read_text().splitlines()[0])
    assert first["event"] == "server_started"


async def test_ac1_connection_snippet_matches_the_server_command():
    snippet = json.load(open("docs/mcp-client.json"))["mcpServers"]["qrypt-entropy"]
    assert snippet["command"] == "uv"
    assert snippet["args"][-2:] == ["-m", "qrypt_entropy.transport.mcp_server"]
    assert snippet["env"]["MCP_CLIENT_KEY"].startswith("REPLACE_WITH")  # the key `make seed` prints for billing-agent
    assert {"DATABASE_URL", "KEY_HASH_SECRET", "PEPPER", "QRYPT_API_KEY"} <= set(snippet["env"])


async def test_ac2_get_random_bytes_returns_contract_shape(tmp_path):
    async with Client(params(tmp_path)) as client:
        result = await client.call_tool("get_random_bytes", {"length": 8, "encoding": "hex"})
    data = result.structured_content
    assert not result.is_error
    assert len(data["value"]) == 16
    assert set(data["metadata"]) == {"source", "batch_id", "batch_ids", "bytes_consumed", "reference"}
    assert data["metadata"]["bytes_consumed"] == 8


async def test_every_tool_and_parameter_is_described_without_values(tmp_path):
    async with Client(params(tmp_path)) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for tool in tools:
        assert (tool.description or "").strip(), tool.name
        for name, schema in tool.input_schema["properties"].items():
            assert schema.get("description", "").strip(), f"{tool.name}.{name}"
        text = json.dumps(tool.input_schema) + tool.description
        assert client_key() not in text
    bounds = {t.name: t.input_schema["properties"] for t in tools}
    assert bounds["get_random_bytes"]["length"]["minimum"] == 1 and bounds["get_random_bytes"]["length"]["maximum"] >= 1
    assert bounds["random_int"]["min"]["maximum"] == 2**53 - 1
    assert bounds["generate_token"]["expiry_seconds"]["anyOf"][0]["minimum"] == 1
