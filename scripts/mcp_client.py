"""Small MCP client for `make mcp-list` and `make mcp-call`: launches the server over stdio like Inspector does.

python scripts/mcp_client.py list
python scripts/mcp_client.py raw     # the server's raw stdout lines: protocol only
python scripts/mcp_client.py call get_random_bytes '{"length":8,"encoding":"hex"}'
"""

import asyncio
import json
import os
import subprocess
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def server_params() -> StdioServerParameters:
    key = os.environ.get("CLIENT_KEY") or os.environ.get("MCP_CLIENT_KEY")  # no default: a missing key is refused
    env = {
        "PATH": os.environ.get("PATH", ""),
        **({"MCP_CLIENT_KEY": key} if key else {}),
        "LOG_FILE": os.environ.get("LOG_FILE", "logs/server.log"),
        **{
            k: os.environ[k]
            for k in (
                "DATABASE_URL",
                "KEY_HASH_SECRET",
                "PEPPER",
                "TOKEN_PEPPER",
                "APP_PROFILE",
                "QRYPT_API_KEY",
                "QRYPT_BASE_URL",
            )  # fmt: skip
            if k in os.environ
        },
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


def raw_session() -> int:
    """Drive the server by hand and print every line it wrote to standard output; fail if one is not JSON-RPC."""
    params = server_params()
    server = subprocess.Popen(  # noqa: S603
        [params.command, *params.args], env=params.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "raw", "version": "0"}}
    call = {"name": "get_random_bytes", "arguments": {"length": 8, "encoding": "hex"}}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": call},
    ]
    server.stdin.write("".join(json.dumps(r) + "\n" for r in requests))
    server.stdin.flush()
    out = []
    while len(out) < 3 and (line := server.stdout.readline()):  # one reply per request that has an id
        out.append(line)
    server.stdin.close()
    server.wait(timeout=30)
    print("".join(out), end="")
    return 0 if len(out) == 3 and all(json.loads(line).get("jsonrpc") == "2.0" for line in out) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["raw"]:
        sys.exit(raw_session())
    if sys.argv[1:2] == ["list"]:
        run = list_tools()
    elif sys.argv[1:2] == ["call"] and len(sys.argv) == 4:
        run = call_tool(sys.argv[2], sys.argv[3])
    else:
        sys.exit("usage: mcp_client.py list | call TOOL JSON_ARGS")
    try:
        sys.exit(asyncio.run(run))
    except Exception:  # a refused key closes the connection; the server's reason is on stderr above
        sys.exit("connection refused by the server (see its message above)")
