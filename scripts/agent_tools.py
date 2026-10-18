"""Dev harness for `make agent-tools`: launch the MCP server the way the agent does and list its tools.

The server needs its own configuration (database, secrets), which a developer's shell supplies here; the agent itself
never passes any (AZ-3).
"""

import asyncio
import os

from qrypt_entropy.agent.mcp_tools import McpTools, server_params

SERVER_CONFIG = (
    "DATABASE_URL",
    "KEY_HASH_SECRET",
    "PEPPER",
    "TOKEN_PEPPER",
    "APP_PROFILE",
    "QRYPT_API_KEY",
    "QRYPT_BASE_URL",
)


async def main() -> None:
    config = {k: os.environ[k] for k in SERVER_CONFIG if k in os.environ}
    async with McpTools(server_params(os.environ.get("MCP_CLIENT_KEY", ""), config)) as tools:
        for spec in tools.specs():
            print(spec["function"]["name"])


if __name__ == "__main__":
    asyncio.run(main())
