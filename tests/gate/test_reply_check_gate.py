"""S27 gate cases: check-honest, check-invented, check-shapes, check-other-conversation, check-zero,
check-no-value-in-log."""

import json

import pytest

from qrypt_entropy.agent.chat import Agent, ModelReply
from qrypt_entropy.agent.reply_check import NOTICE, check_reply
from qrypt_entropy.agent.scripted import ScriptedModel, StubTools, call_step, reset_code_demo
from qrypt_entropy.logs import configure_logging


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "agent.log"
    configure_logging("INFO", str(path))
    yield path
    configure_logging()


def checks(path) -> list[dict]:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]
    return [e for e in lines if e["event"] == "reply_check"]


def invented(reply: str) -> Agent:
    return Agent(ScriptedModel([ModelReply(reply)]), StubTools({}))


async def test_check_honest(log_file):
    agent, _ = reset_code_demo()
    assert await agent.turn("a reset code") == "Your code is 493021"
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(1, 1)]


async def test_check_invented(log_file):
    reply = await invented("Your code is 731905").turn("a reset code")
    assert "731905" not in reply and NOTICE in reply
    assert "no value was issued" in reply and "Ask me" in reply
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(1, 0)]


@pytest.mark.parametrize(
    "value",
    [
        "731905",
        "9f2c4e6a8b0d1f3a5c7e9b2d4f6a8c0e1b3d5f7a9c2e4b6d8f0a1c3e5b7d9f21",
        "3b241101-e2bb-4255-8caf-4136c566a962",
        "4155550134",
    ],
)
async def test_check_shapes(value, log_file):
    reply = await invented(f"Here you go: {value}.").turn("x")
    assert value not in reply and NOTICE in reply
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(1, 0)]


async def test_check_other_conversation(log_file):
    first, _ = reset_code_demo()
    code = await first.turn("a reset code")
    second = invented(f"Your earlier code was {code[-6:]}")
    reply = await second.turn("what was my code?")
    assert "493021" not in reply and NOTICE in reply
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(1, 1), (1, 0)]


async def test_check_zero(log_file):
    reply = await invented("I cannot make up a code, but I can issue a real one.").turn("make one up")
    assert reply == "I cannot make up a code, but I can issue a real one."
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(0, 0)]


async def test_check_no_value_in_log(log_file):
    await invented("Your code is 731905").turn("a reset code")
    assert "731905" not in log_file.read_text()


async def test_backed_and_invented_in_one_reply(log_file):
    steps = [call_step("generate_token", kind="otp_6"), ModelReply("Code {{v1}}, backup 731905")]
    reply = await Agent(ScriptedModel(steps), StubTools({"generate_token": 493021})).turn("x")
    assert reply.startswith("Code 493021, backup ") and "731905" not in reply
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(2, 1)]


def test_ordinary_text_is_not_a_value():
    held = {}
    for text in ("Up to 1000 requests a day, 20 a minute, in 600 seconds.", "On 2026-10-18 at 12:30.", "v1.2.3 is out"):
        assert check_reply(text, held) == text


async def test_honest_reply_quoting_batch_and_reference_is_unchanged(log_file):
    steps = [
        call_step("generate_token", kind="otp_6"),
        ModelReply("Your code is {{v1}} (batch 00000000-0000-4000-8000-0000000000b1, "
                   "reference 00000000-0000-4000-8000-0000000000f1)"),
    ]  # fmt: skip
    reply = await Agent(ScriptedModel(steps), StubTools({"generate_token": 493021})).turn("x")
    assert NOTICE not in reply and "00000000-0000-4000-8000-0000000000f1" in reply
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(3, 3)]


@pytest.mark.parametrize("text", ["OTP-731905", "code=731905", "/codes/731905/x", "+731905", "ref_731905", "(731905)"])
def test_value_glued_to_text_is_caught(text):
    assert "731905" not in check_reply(text, {}) and NOTICE in check_reply(text, {})


def test_backed_value_glued_to_text_stays():
    assert check_reply("OTP-731905", {"v1": "731905"}) == "OTP-731905"


@pytest.mark.parametrize(
    "value", ["abcdef" * 6, "ABCDEFABCDEFABCDEFABCDEFABCDEFABCD", "abcdefab-cdef-abcd-efab-cdefabcdefab"]
)
def test_all_letter_hex_and_uuid_are_caught(value):
    assert value not in check_reply(f"key {value}", {})


async def test_fixed_replies_emit_exactly_one_reply_check(log_file):
    agent = Agent(ScriptedModel([call_step("random_int", min=1, max=2)] * 6), StubTools({"random_int": 1}))
    assert "stopped" in await agent.turn("loop")  # the loop-bound reply is fixed text
    assert [(e["found"], e["backed"]) for e in checks(log_file)] == [(0, 0)]
