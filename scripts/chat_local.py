"""Dev harness for `make chat`: the interactive agent with the real OpenRouter model against the local stack.

Like scripts/agent_tools.py it forwards the server's own configuration to the MCP server it launches; the agent code
itself never holds any (AZ-3). Needs OPENROUTER_API_KEY and MCP_CLIENT_KEY (the billing-agent key from `make seed`).
"""

import asyncio
import os
import sys

from agent_tools import SERVER_CONFIG

from qrypt_entropy.agent.__main__ import chat
from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import configure_logging

if __name__ == "__main__":
    settings = AgentSettings()
    if not settings.openrouter_api_key:
        sys.exit("OPENROUTER_API_KEY is not set: this is the real-model run (docs/DEPLOY.md, section S32).")
    configure_logging(settings.log_level, settings.agent_log_file or None)
    sys.exit(
        asyncio.run(chat(" ".join(sys.argv[1:]) or None, {k: os.environ[k] for k in SERVER_CONFIG if k in os.environ}))
    )
