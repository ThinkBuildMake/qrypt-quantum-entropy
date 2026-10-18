"""Test doubles: a scripted model and a stub tool set (EV-4). No network, no credentials."""

import copy
import json
import re

from qrypt_entropy.agent.chat import PLACEHOLDER, Agent, ModelReply, ToolCall, ToolResult

STUB_METADATA = {
    "source": "stub",
    "batch_id": "00000000-0000-4000-8000-0000000000b1",
    "batch_ids": ["00000000-0000-4000-8000-0000000000b1"],
    "bytes_consumed": 4,
    "reference": "00000000-0000-4000-8000-0000000000f1",
}
STUB_SPECS = [
    {"type": "function", "function": {"name": name, "description": "stub", "parameters": {"type": "object"}}}
    for name in ("get_random_bytes", "random_int", "generate_token")
]


class ScriptedModel:
    """Replies from a fixed script, one step per request, and keeps a copy of every request it was sent."""

    name = "scripted"

    def __init__(self, steps: list[ModelReply]):
        self._steps = list(steps)
        self.requests: list[dict] = []

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        return self._steps.pop(0)


class StubTools:
    """Returns a fixed value per tool and records the calls it received."""

    def __init__(self, values: dict[str, str | int]):
        self.values, self.calls = values, []

    def specs(self) -> list[dict]:
        return STUB_SPECS

    async def call(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(value=self.values[name], metadata=STUB_METADATA)


def call_step(name: str, **arguments) -> ModelReply:
    return ModelReply(None, [ToolCall("call_1", name, arguments)])


def reset_code_demo() -> tuple[Agent, ScriptedModel]:
    """The AC1 run: the model asks for an otp_6 for user-42 (600 s), then repeats the placeholder."""
    steps = [
        call_step("generate_token", kind="otp_6", owner="user-42", expiry_seconds=600),
        ModelReply("Your code is {{v1}}"),
    ]
    model = ScriptedModel(steps)
    return Agent(model, StubTools({"generate_token": 493021})), model


VALUE_SHAPES = re.compile(
    r"(?<![\w-])(\d{6}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{16,}|[A-Za-z0-9+/_-]{20,}={0,2})(?![\w-])"
)


def capture_problems(requests: list[dict]) -> list[str]:
    """What in the captured model requests could be an issued value: a tool message whose value is not a placeholder,
    or a user or assistant text holding a 6-digit code, a UUID, or a long hex or base64 string."""
    problems = []
    for n, request in enumerate(requests, 1):
        for message in request["messages"]:
            if message["role"] == "tool":
                value = json.loads(message["content"]).get("value")
                if value is not None and not (isinstance(value, str) and PLACEHOLDER.fullmatch(value)):
                    problems.append(f"request {n}: a tool result holds a value that is not a placeholder")
            elif message["role"] in ("user", "assistant") and VALUE_SHAPES.search(message.get("content") or ""):
                problems.append(f"request {n}: a {message['role']} message holds a value-shaped string")
    return problems
