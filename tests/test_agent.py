import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from qrypt_entropy.agent.chat import Agent, ModelReply, ToolCall, ToolResult
from qrypt_entropy.agent.mcp_tools import McpTools, server_params
from qrypt_entropy.agent.openrouter import OpenRouterModel
from qrypt_entropy.agent.scripted import ScriptedModel, StubTools, call_step
from qrypt_entropy.agent.settings import AgentSettings
from tests.conftest import server_env


async def test_ac3_agent_tools_lists_the_three_tools_and_nothing_that_reads_stored_data(tmp_path):
    async with McpTools(
        server_params("stub-client-key", {"LOG_FILE": str(tmp_path / "s.log"), **server_env()})
    ) as tools:
        names = {s["function"]["name"] for s in tools.specs()}
    assert names == {"get_random_bytes", "random_int", "generate_token"}


async def test_real_server_call_returns_value_and_metadata_for_the_agent(tmp_path):
    config = {"LOG_FILE": str(tmp_path / "s.log"), **server_env()}
    async with McpTools(server_params("stub-client-key", config)) as tools:
        result = await tools.call("random_int", {"min": 1, "max": 6})
    assert 1 <= result.value <= 6
    assert result.error is None and "reference" in result.metadata


async def test_tool_error_reaches_the_model_without_a_value(tmp_path):
    config = {"LOG_FILE": str(tmp_path / "s.log"), **server_env()}
    async with McpTools(server_params("stub-client-key", config)) as tools:
        result = await tools.call("random_int", {"min": 5, "max": 1})
    assert result.value is None and result.error


def run_agent(cwd, *args):
    env = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    return subprocess.run(
        [sys.executable, "-m", "qrypt_entropy.agent", *args], cwd=cwd, capture_output=True, text=True, env=env
    )


def test_make_agent_scripted_prints_the_reply_and_saves_the_capture(tmp_path):
    out = run_agent(tmp_path, "scripted", "issue a code")
    assert out.stdout.strip() == "Your code is 493021"
    assert "493021" not in (tmp_path / "logs" / "agent-capture.json").read_text()
    assert run_agent(tmp_path, "capture-check", "logs/agent-capture.json").returncode == 0


def test_capture_check_fails_on_a_bare_value(tmp_path):
    bad = [{"messages": [{"role": "tool", "content": json.dumps({"value": 493021})}]}]
    (tmp_path / "c.json").write_text(json.dumps(bad))
    assert run_agent(tmp_path, "capture-check", "c.json").returncode == 1


async def test_unknown_placeholder_is_left_alone_and_loop_is_bounded():
    agent = Agent(ScriptedModel([call_step("random_int", min=1, max=2)] * 6), StubTools({"random_int": 1}))
    assert "stopped" in await agent.turn("loop")
    assert agent.model_requests == 6


async def test_openrouter_model_sends_one_request_and_parses_a_tool_call():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        call = {"id": "c1", "type": "function", "function": {"name": "random_int", "arguments": '{"min":1,"max":6}'}}
        body = {"id": "x", "object": "chat.completion", "created": 0, "model": "m"}
        body["choices"] = [
            {"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [call]}}
        ]
        return httpx.Response(200, json=body)

    model = OpenRouterModel(
        AgentSettings(agent_model="some/model:free"), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    reply = await model.complete([{"role": "user", "content": "roll"}], [])
    assert len(seen) == 1 and seen[0]["model"] == "some/model:free"
    assert reply.tool_calls[0].name == "random_int" and reply.tool_calls[0].arguments == {"min": 1, "max": 6}


def test_capture_check_flags_value_shaped_text(tmp_path):
    bad = [{"messages": [{"role": "user", "content": "my code is 493021"}]}]
    (tmp_path / "c.json").write_text(json.dumps(bad))
    assert run_agent(tmp_path, "capture-check", "c.json").returncode == 1


async def test_missing_value_is_an_error_not_none():
    class Odd:
        def specs(self):
            return []

        async def call(self, name, arguments):
            return ToolResult(error="the tool returned no value")

    agent = Agent(ScriptedModel([call_step("random_int", min=1, max=2), ModelReply("sorry")]), Odd())
    assert await agent.turn("x") == "sorry"
    assert agent.values == {}


async def test_malformed_tool_arguments_get_a_plain_error_and_last_round_offers_no_tools():
    steps = [ModelReply(None, [ToolCall("c1", "random_int", None)])] * 6
    model = ScriptedModel(steps)
    reply = await Agent(model, StubTools({})).turn("x")
    assert "stopped" in reply
    assert model.requests[-1]["tools"] == [] and model.requests[0]["tools"] != []
    assert "not valid JSON" in model.requests[1]["messages"][-1]["content"]
