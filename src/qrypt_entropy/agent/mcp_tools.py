"""The MCP server's tools, over stdio. The launcher gives the server its client key and nothing else (AZ-3, A-14)."""

import json
import os
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment

from qrypt_entropy.agent.chat import ToolResult


def server_params(client_key: str, server_config: Mapping[str, str] | None = None) -> StdioServerParameters:
    """`server_config` is the server's own configuration for a dev harness or test; the agent itself never has any."""
    env = {"PATH": os.environ.get("PATH", ""), "MCP_CLIENT_KEY": client_key, **(server_config or {})}
    return StdioServerParameters(command=sys.executable, args=["-m", "qrypt_entropy.transport.mcp_server"], env=env)


def child_environment(params: StdioServerParameters) -> dict[str, str]:
    """The environment the SDK gives the launched server."""
    return get_default_environment() | (params.env or {})


class McpTools:
    """Async context manager: starts the server, lists its tools, calls them."""

    def __init__(self, params: StdioServerParameters):
        self.params = params
        self._stack = AsyncExitStack()
        self._tools: list[dict] = []

    async def __aenter__(self) -> McpTools:
        try:
            self._client = await self._stack.enter_async_context(Client(self.params))
            listed = await self._client.list_tools()
        except BaseException:
            await self._stack.aclose()
            raise
        self._tools = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema},
            }
            for t in listed.tools
        ]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def specs(self) -> list[dict]:
        return self._tools

    async def call(self, name: str, arguments: dict) -> ToolResult:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            return ToolResult(error=_error_text(result.content[0].text if result.content else ""))
        data = result.structured_content or {}
        if data.get("value") is None:
            return ToolResult(error="the tool returned no value")
        return ToolResult(value=data.get("value"), metadata=data.get("metadata", {}))


def _error_text(text: str) -> str:
    """The server's ErrorBody message, never more than that."""
    try:
        return str(json.loads(text).get("message") or "the tool failed")
    except ValueError, AttributeError:
        return "the tool failed"
