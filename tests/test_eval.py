"""S40: the evaluation logic (src/qrypt_entropy/agent/evaluation.py) against scripted models and tools. No network."""

import json

import pytest

from qrypt_entropy.agent.chat import Agent, ModelReply, ToolCall, ToolResult
from qrypt_entropy.agent.evaluation import (
    Recorder,
    RecordingModel,
    RequestLimitReached,
    adversarial_verdict,
    capture_matches,
    golden_verdict,
    load_cases,
    p95,
    unbacked_values,
)
from qrypt_entropy.agent.scripted import STUB_METADATA, ScriptedModel, StubTools, call_step

GOLDEN = load_cases("evals/golden.json")
ADVERSARIAL = load_cases("evals/adversarial.json")


def test_a_bad_rule_is_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cases": [{"id": "X", "prompt": "p", "tool": "random_int", "rules": [{"arg": "min"}]}]}))
    with pytest.raises(ValueError, match="rule"):
        load_cases(str(bad))


def call(name, **arguments):
    return {"name": name, "arguments": arguments, "error": None, "value": "1", "metadata": {}}


CASE = next(c for c in GOLDEN if c["id"] == "G01")


def test_golden_pass_and_each_way_to_fail():
    ok = call("generate_token", kind="otp_6", owner="user-42", expiry_seconds=600)
    assert golden_verdict(CASE, [ok]) == (True, "")
    assert not golden_verdict(CASE, [])[0]  # no tool called
    assert "wrong tool" in golden_verdict(CASE, [call("random_int", min=1, max=2)])[1]
    assert "expiry_seconds" in golden_verdict(CASE, [call("generate_token", kind="otp_6", owner="user-42")])[1]
    assert (
        "kind" in golden_verdict(CASE, [call("generate_token", kind="uuid_v4", owner="user-42", expiry_seconds=600)])[1]
    )
    assert "2 tool calls" in golden_verdict(CASE, [ok, ok])[1]
    assert "refused" in golden_verdict(CASE, [{**ok, "error": "bad"}])[1]


def test_range_match_and_absent_rules():
    uuid_case = next(c for c in GOLDEN if c["id"] == "G06")
    assert golden_verdict(uuid_case, [call("generate_token", kind="uuid_v4")])[0]
    assert "owner" in golden_verdict(uuid_case, [call("generate_token", kind="uuid_v4", owner="x")])[1]


def test_unbacked_values_finds_only_what_no_tool_returned():
    calls = [{**call("generate_token"), "value": "493021", "metadata": STUB_METADATA}]
    assert unbacked_values("Your code is 493021", calls) == []
    assert unbacked_values("Your code is 731905", calls) == ["731905"]
    assert unbacked_values("batch " + STUB_METADATA["batch_id"], calls) == []


def test_unbacked_value_in_a_random_int_reply():
    case = next(c for c in GOLDEN if c["id"] == "G11")
    calls = [{**call("random_int", min=1, max=100), "value": "57", "metadata": {"bytes_consumed": 1}}]
    assert unbacked_values("You got 57", calls, case["prompt"]) == []
    assert unbacked_values("You got 58", calls, case["prompt"]) == ["58"]
    assert unbacked_values("between 1 and 100: 57", calls, case["prompt"]) == []


def test_adversarial_verdicts():
    secrets = ["pepper-abc123"]
    assert adversarial_verdict("I can't do that.", ["I can't do that."], [], secrets, 5, 5) == (True, "")
    assert "tool call" in adversarial_verdict("no", ["no"], [call("random_int")], secrets, 5, 5)[1]
    assert "value" in adversarial_verdict("Code 731905", ["x"], [], secrets, 5, 5)[1]
    assert "value" in adversarial_verdict("ok", ["Code 731905"], [], secrets, 5, 5)[1]
    assert "secret" in adversarial_verdict("it is pepper-abc123", ["x"], [], secrets, 5, 5)[1]
    assert "cap" in adversarial_verdict("ok", ["ok"], [], secrets, 5, 6)[1]


def test_p95_is_nearest_rank():
    assert p95([1.0] * 14 + [9.0]) == 9.0
    assert p95([float(i) for i in range(1, 101)]) == 95.0


def test_capture_matches_searches_every_message_for_long_values_and_model_text_for_short_ones():
    capture = [
        {
            "messages": [
                {"role": "tool", "content": json.dumps({"value": "{{v1}}", "metadata": {"bytes_consumed": 57}})},
                {"role": "user", "content": "code 731905 between 1 and 100"},
                {"role": "assistant", "content": "I rolled 58 and 100"},
            ]
        }
    ]
    assert capture_matches(capture, ["731905"]) == ["731905"]
    assert capture_matches(capture, ["493021"]) == []
    assert capture_matches(capture, ["57"]) == []  # in a tool message only: metadata, not a value
    assert capture_matches(capture, ["58"]) == ["58"]
    assert capture_matches(capture, ["100"], "between 1 and 100") == []  # the prompt holds that number itself


async def test_recording_model_counts_requests_and_stops_at_the_limit():
    inner = ScriptedModel([ModelReply("a"), ModelReply("b")])
    model = RecordingModel(inner, min_interval_s=0, limit=1)
    await model.complete([{"role": "user", "content": "x"}], [])
    assert model.requests == 1 and model.raw_replies == ["a"] and model.captured[0]["messages"][0]["content"] == "x"
    with pytest.raises(RequestLimitReached):
        await model.complete([], [])


async def test_recorder_keeps_every_call_and_return():
    tools = Recorder(StubTools({"random_int": 7}))
    result = await tools.call("random_int", {"min": 1, "max": 9})
    assert result.value == 7 and tools.calls[0]["arguments"] == {"min": 1, "max": 9} and tools.calls[0]["value"] == "7"


async def test_an_agent_run_through_the_recorders_gives_a_passing_golden_case():
    tools = Recorder(StubTools({"generate_token": 493021}))
    model = RecordingModel(
        ScriptedModel(
            [
                call_step("generate_token", kind="otp_6", owner="user-42", expiry_seconds=600),
                ModelReply("Your code is {{v1}}"),
            ]
        ),
        min_interval_s=0,
        limit=10,
    )
    reply = await Agent(model, tools).turn(CASE["prompt"])
    assert reply == "Your code is 493021" and golden_verdict(CASE, tools.calls)[0]
    assert unbacked_values(reply, tools.calls) == []
    assert ToolCall and ToolResult  # (imports used by the doubles)
