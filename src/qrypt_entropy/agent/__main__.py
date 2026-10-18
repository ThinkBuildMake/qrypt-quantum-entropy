"""Command-line chat.

python -m qrypt_entropy.agent                 interactive chat with the real model and the real MCP server
python -m qrypt_entropy.agent "text"          one request
python -m qrypt_entropy.agent scripted "text" the scripted model and a stub tool (no network); saves the capture
python -m qrypt_entropy.agent capture-check F check a saved capture holds placeholders only
"""

import asyncio
import json
import sys
from pathlib import Path

from qrypt_entropy.agent.chat import Agent
from qrypt_entropy.agent.mcp_tools import McpTools, server_params
from qrypt_entropy.agent.openrouter import OpenRouterModel
from qrypt_entropy.agent.scripted import capture_problems, reset_code_demo
from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import configure_logging

CAPTURE_FILE = "logs/agent-capture.json"


async def scripted(text: str) -> int:
    agent, model = reset_code_demo()
    print(await agent.turn(text))
    agent.finish()
    Path(CAPTURE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(CAPTURE_FILE).write_text(json.dumps(model.requests, indent=2))
    return 0


def capture_check(path: str) -> int:
    requests = json.loads(Path(path).read_text())
    problems = capture_problems(requests)
    print("\n".join(problems) or f"{len(requests)} model requests: placeholders only")
    return 1 if problems else 0


async def chat(first: str | None, server_config: dict[str, str] | None = None) -> int:
    """`server_config` is for a dev harness (scripts/chat_local.py); the agent itself passes the server nothing."""
    settings = AgentSettings()
    if not settings.mcp_client_key:
        print("MCP_CLIENT_KEY is not set, so the tools cannot be started.")
        return 1
    async with McpTools(server_params(settings.mcp_client_key, server_config)) as tools:
        agent = Agent(OpenRouterModel(settings), tools, settings)
        try:
            lines = iter([first]) if first else iter(lambda: input("> ").strip(), "")
            for line in lines:
                print(await agent.turn(line))
        except EOFError:
            pass
        finally:
            agent.finish()
    return 0


def main(argv: list[str]) -> int:
    settings = AgentSettings()
    configure_logging(settings.log_level, settings.agent_log_file or None)
    match argv:
        case ["capture-check", path]:
            return capture_check(path)
        case ["scripted", *words]:
            return asyncio.run(scripted(" ".join(words)))
        case _:
            return asyncio.run(chat(" ".join(argv) or None))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
