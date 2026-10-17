"""Small MCP client for `make mcp-list` and `make mcp-call`: launches the server over stdio like Inspector does.

python scripts/mcp_client.py list
python scripts/mcp_client.py call get_random_bytes '{"length":8,"encoding":"hex"}'
"""

import asyncio
import json
import os
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def server_params() -> StdioServerParameters:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "MCP_CLIENT_KEY": os.environ.get("MCP_CLIENT_KEY", "stub-client-key"),
        "LOG_FILE": os.environ.get("LOG_FILE", "logs/server.log"),
        **{k: os.environ[k] for k in ("DATABASE_URL", "KEY_HASH_SECRET") if k in os.environ},
    }
    return StdioServerParameters(command=sys.executable, args=["-m", "qrypt_entropy.transport.mcp_server"], env=env)


async def list_tools() -> int:
    async with Client(server_params()) as client:
        for tool in (await client.list_tools()).tools:
            props = tool.input_schema.get("properties", {})
            inputs = ", ".join(f"{n}: {p.get('type') or p.get('enum') or p.get('anyOf')}" for n, p in props.items())
            print(f"{tool.name}({inputs})")
    return 0


async def call_tool(name: str, raw_args: str) -> int:
    async with Client(server_params()) as client:
        result = await client.call_tool(name, json.loads(raw_args))
    print(json.dumps(result.structured_content if not result.is_error else result.content[0].text, indent=2))
    return 1 if result.is_error else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["list"]:
        sys.exit(asyncio.run(list_tools()))
    if sys.argv[1:2] == ["call"] and len(sys.argv) == 4:
        sys.exit(asyncio.run(call_tool(sys.argv[2], sys.argv[3])))
    sys.exit("usage: mcp_client.py list | call TOOL JSON_ARGS")
