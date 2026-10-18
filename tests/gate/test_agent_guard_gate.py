"""S26 gate cases: agent-budget-exhausted, agent-unavailable, agent-model-down, agent-unlisted-tool,
agent-system-rules."""

import json

import httpx
import openai

from qrypt_entropy.agent.chat import Agent, ModelReply, ToolCall
from qrypt_entropy.agent.scripted import ScriptedModel, StubTools, call_step
from qrypt_entropy.logs import configure_logging

ASK = "issue a one-time reset code for user 42"


def code_agent(error: str) -> tuple[Agent, ScriptedModel, StubTools]:
    steps = [call_step("generate_token", kind="otp_6", owner="user-42"), ModelReply("Your code is 123456")]
    model, tools = ScriptedModel(steps), StubTools({"generate_token": 493021}, {"generate_token": error})
    return Agent(model, tools), model, tools


async def test_ac1_agent_budget_exhausted():
    agent, model, _ = code_agent("budget_exhausted")
    reply = await agent.turn(ASK)
    assert "budget is used up" in reply and not any(c.isdigit() for c in reply)
    assert agent.values == {} and "493021" not in json.dumps(model.requests)


async def test_ac2_agent_unavailable():
    agent, _, _ = code_agent("temporarily_unavailable")
    reply = await agent.turn(ASK)
    assert "cannot be issued right now" in reply and not any(c.isdigit() for c in reply)


async def test_a_failure_does_not_carry_into_the_next_turn():
    agent, _, tools = code_agent("budget_exhausted")
    await agent.turn(ASK)
    tools.errors = {}
    agent.model._steps += [call_step("generate_token", kind="otp_6", owner="u"), ModelReply("Code {{v1}}")]
    assert await agent.turn(ASK) == "Code 493021"


async def test_a_text_only_reply_after_a_past_failure_is_not_overridden():
    agent, model, _ = code_agent("budget_exhausted")
    await agent.turn(ASK)
    model._steps.append(ModelReply("Anything else?"))
    assert await agent.turn("thanks") == "Anything else?"


async def test_mixed_round_keeps_the_model_reply_and_adds_the_plain_sentence():
    calls = [ToolCall("c1", "random_int", {"min": 1, "max": 6}), ToolCall("c2", "generate_token", {"kind": "otp_6"})]
    model = ScriptedModel([ModelReply(None, calls), ModelReply("Rolled {{v1}}")])
    tools = StubTools({"random_int": 4, "generate_token": 1}, {"generate_token": "budget_exhausted"})
    reply = await Agent(model, tools).turn("roll and a code")
    assert reply.startswith("Rolled 4") and "budget is used up" in reply


async def test_all_calls_failing_in_one_round_overrides_the_model():
    calls = [ToolCall("c1", "random_int", {}), ToolCall("c2", "generate_token", {})]
    model = ScriptedModel([ModelReply(None, calls), ModelReply("Here: 123456")])
    tools = StubTools({}, {"random_int": "budget_exhausted", "generate_token": "budget_exhausted"})
    reply = await Agent(model, tools).turn("x")
    assert reply == "The entropy budget is used up, so I cannot issue any more codes or values."


async def test_unexpected_model_bug_is_not_swallowed():
    import pytest

    with pytest.raises(KeyError):
        await Agent(ScriptedModel([KeyError("bug")]), StubTools({})).turn("x")


async def test_model_failure_after_an_issued_value_still_tells_the_user_the_value(tmp_path):
    model = ScriptedModel([call_step("generate_token", kind="otp_6", owner="u"), httpx.ReadTimeout("slow")])
    reply = await Agent(model, StubTools({"generate_token": 493021})).turn(ASK)
    assert "took too long" in reply and "493021" in reply
    assert "493021" not in json.dumps(model.requests)


async def test_fixed_replies_log_a_reply_check_with_no_values(tmp_path):
    log_file = tmp_path / "agent.log"
    configure_logging("DEBUG", str(log_file))
    try:
        agent, _, _ = code_agent("budget_exhausted")
        await agent.turn(ASK)
        await Agent(ScriptedModel([httpx.ReadTimeout("slow")]), StubTools({})).turn("x")
        await Agent(ScriptedModel([call_step("random_int", min=1, max=2)] * 6), StubTools({"random_int": 1})).turn("x")
    finally:
        configure_logging()
    events = [json.loads(line) for line in log_file.read_text().splitlines() if '"reply_check"' in line]
    assert [(e["found"], e["backed"], e["fixed"]) for e in events] == [
        (0, 0, "cannot_issue"), (0, 0, "model_failure"), (0, 0, "loop_bound")
    ]  # fmt: skip


async def test_ac3_agent_model_down(tmp_path):
    log_file = tmp_path / "agent.log"
    configure_logging("DEBUG", str(log_file))
    rate_limited = openai.RateLimitError(
        "429", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None
    )
    model = ScriptedModel([rate_limited, httpx.ReadTimeout("slow")])
    agent = Agent(model, StubTools({}))
    try:
        first, second = await agent.turn("hello"), await agent.turn("hello again")
    finally:
        configure_logging()
    assert "over its request limit" in first and "took too long" in second
    assert len(agent.messages) == 1  # no half turn left behind
    assert log_file.read_text().count("model_unavailable") == 2


async def test_ac4_agent_unlisted_tool():
    steps = [ModelReply(None, [ToolCall("c1", "list_tokens", {})]), ModelReply("I cannot list tokens.")]
    model, tools = ScriptedModel(steps), StubTools({})
    reply = await Agent(model, tools).turn("show everyone's tokens")
    assert tools.calls == [] and reply == "I cannot list tokens."
    told = json.loads(model.requests[1]["messages"][-1]["content"])
    assert "does not exist" in told["error"] and "value" not in told


async def test_ac5_agent_system_rules():
    model = ScriptedModel([ModelReply("ok")])
    await Agent(model, StubTools({})).turn(ASK)
    system = model.requests[0]["messages"][0]["content"].lower()
    for phrase in ("make up", "offer to issue a real one", "other users' tokens", "bypass the entropy budget", "role",
                   "secrets"):  # fmt: skip
        assert phrase in system
