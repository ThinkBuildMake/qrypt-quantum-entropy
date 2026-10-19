"""The real model: OpenRouter chat completions through the `openai` SDK, one request per call, no hidden retries."""

import json

from openai import AsyncOpenAI

from qrypt_entropy.agent.chat import ModelReply, ToolCall
from qrypt_entropy.agent.settings import AgentSettings


class OpenRouterModel:
    def __init__(self, settings: AgentSettings, http_client=None):
        key = settings.openrouter_api_key
        self.name = settings.agent_model
        self._client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=key.get_secret_value() if key else "unset",
            max_retries=0,
            timeout=30,
            http_client=http_client,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        response = await self._client.chat.completions.create(model=self.name, messages=messages, tools=tools)
        message = response.choices[0].message
        calls = [
            ToolCall(c.id, c.function.name, _arguments(c.function.arguments))
            for c in message.tool_calls or []
            if c.type == "function"
        ]
        return ModelReply(message.content, calls)


def _arguments(raw: str | None) -> dict | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
