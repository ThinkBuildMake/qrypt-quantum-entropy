"""Agent: the Secure Token Agent's chat loop. Owns nothing; holds no database connection.

The model only ever sees placeholders such as {{v1}}. The agent keeps each issued value in memory and puts it into the
reply itself (CP-5). Every model request is counted (GC-3). Plain-words failures are S26; the reply check is S27.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import get_logger

log = get_logger("agent")
PLACEHOLDER = re.compile(r"\{\{v(\d+)\}\}")
MAX_ROUNDS = 6  # model requests allowed within one turn
FREE_TIER_LIMIT = "20 requests a minute, 1000 a day"  # GC-3: OpenRouter :free models with the $10 credit
SYSTEM_PROMPT = (
    "You are the Secure Token Agent. To issue anything, call a tool. A tool result holds a placeholder such as "
    "{{v1}} instead of the real value: write that placeholder exactly in your reply, and never invent a code, key "
    "or number yourself."
)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None: the model's arguments were not a JSON object


@dataclass(frozen=True)
class ModelReply:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolResult:
    value: str | int | None = None
    metadata: dict = field(default_factory=dict)
    error: str | None = None


class Model(Protocol):
    name: str

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


class Tools(Protocol):
    def specs(self) -> list[dict]: ...

    async def call(self, name: str, arguments: dict) -> ToolResult: ...


class Agent:
    """One conversation: the messages, the held values and the count of model requests."""

    def __init__(self, model: Model, tools: Tools, settings: AgentSettings | None = None):
        self.model, self.tools = model, tools
        self.settings = settings or AgentSettings()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.values: dict[str, str] = {}  # placeholder number -> issued value; memory only
        self.model_requests = 0

    async def turn(self, text: str) -> str:
        self.messages.append({"role": "user", "content": self._hide(text)})
        for round_ in range(MAX_ROUNDS):
            last = round_ == MAX_ROUNDS - 1
            self.model_requests += 1
            log.info("model_request", n=self.model_requests)
            reply = await self.model.complete(self.messages, [] if last else self.tools.specs())
            self.messages.append(_assistant_message(reply))
            if last and reply.tool_calls:
                break
            if not reply.tool_calls:
                return self._fill(reply.content or "")
            for call in reply.tool_calls:
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": await self._run_tool(call)})
        return "I could not finish that request: the model kept asking for more tool calls, so I stopped."

    def _hide(self, text: str) -> str:
        """Nothing the user types may carry an issued value to the provider: swap held values for their placeholders."""
        for key, value in sorted(self.values.items(), key=lambda kv: -len(kv[1])):
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", "{{" + key + "}}", text)
        return text

    async def _run_tool(self, call: ToolCall) -> str:
        """Call the tool and return what the model may see: a placeholder and the metadata, never the value."""
        if call.arguments is None:
            return json.dumps({"error": "the tool arguments were not valid JSON; call again with a JSON object"})
        arguments = dict(call.arguments)
        if call.name == "generate_token" and arguments.get("expiry_seconds") is None:
            arguments["expiry_seconds"] = self.settings.token_expiry_default_seconds  # FR-2.1
        result = await self.tools.call(call.name, arguments)
        log.info("agent_tool_call", tool=call.name, ok=result.error is None)
        if result.error is not None:
            return json.dumps({"error": result.error})
        key = f"v{len(self.values) + 1}"
        self.values[key] = str(result.value)
        return json.dumps({"value": "{{" + key + "}}", "metadata": result.metadata})

    def _fill(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda m: self.values.get(f"v{m.group(1)}", m.group(0)), text)

    def finish(self) -> None:
        """Write the run's request count to the results file (GC-3)."""
        path = Path(self.settings.agent_results_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"model": self.model.name, "model_requests": self.model_requests, "free_tier_limit": FREE_TIER_LIMIT},
                indent=2,
            )
            + "\n"
        )
        log.info("run_finished", model_requests=self.model_requests)


def _assistant_message(reply: ModelReply) -> dict:
    message: dict = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in reply.tool_calls
        ]
    return message
