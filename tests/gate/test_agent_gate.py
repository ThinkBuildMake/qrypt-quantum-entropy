"""S25 gate cases: agent-no-value-to-model, agent-default-expiry, agent-request-count, agent-child-env."""

import json
import subprocess
import sys

from qrypt_entropy.agent.chat import Agent, ModelReply
from qrypt_entropy.agent.mcp_tools import child_environment, server_params
from qrypt_entropy.agent.scripted import ScriptedModel, StubTools, call_step, reset_code_demo
from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import configure_logging
from tools.check_imports import find_violations

REQUEST = "issue a one-time reset code for user 42 that expires in 10 minutes"


async def test_ac1_plain_request_prints_the_real_value():
    agent, _ = reset_code_demo()
    assert await agent.turn(REQUEST) == "Your code is 493021"


async def test_ac2_agent_no_value_to_model():
    agent, model = reset_code_demo()
    await agent.turn(REQUEST)
    captured = json.dumps(model.requests)
    assert "493021" not in captured
    tool_message = next(m for r in model.requests for m in r["messages"] if m["role"] == "tool")
    body = json.loads(tool_message["content"])
    assert body["value"] == "{{v1}}"
    assert body["metadata"]["bytes_consumed"] == 4


async def test_ac4_agent_default_expiry():
    tools = StubTools({"generate_token": 493021})
    model = ScriptedModel(
        [call_step("generate_token", kind="otp_6", owner="user-42"), ModelReply("Your code is {{v1}}")]
    )
    await Agent(model, tools).turn("a reset code for user 42")
    assert tools.calls == [("generate_token", {"kind": "otp_6", "owner": "user-42", "expiry_seconds": 600})]


async def test_ac5_agent_request_count(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RESULTS_FILE", str(tmp_path / "results" / "agent_run.json"))
    steps = [
        call_step("random_int", min=1, max=6),
        ModelReply("You rolled {{v1}}"),
        ModelReply("Anything else?"),
        call_step("generate_token", kind="uuid_v4"),
        ModelReply("Here: {{v2}}"),
    ]
    agent = Agent(ScriptedModel(steps), StubTools({"random_int": 4, "generate_token": "x"}))
    for text in ("roll a die", "thanks", "a uuid"):
        await agent.turn(text)
    agent.finish()
    saved = json.loads((tmp_path / "results" / "agent_run.json").read_text())
    assert saved["model_requests"] == 5 and saved["model"] == "scripted"
    assert saved["free_tier_limit"] == "20 requests a minute, 1000 a day"


def test_ac6_agent_child_env(monkeypatch):
    for name in ("DATABASE_URL", "PEPPER", "KEY_HASH_SECRET", "QRYPT_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "must-not-reach-the-child")
    env = child_environment(server_params("the-client-key"))
    listing = subprocess.run(  # the real child sees exactly this environment
        [sys.executable, "-I", "-c", "import json,os;print(json.dumps(sorted(os.environ)))"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    names = set(json.loads(listing))
    assert "MCP_CLIENT_KEY" in names
    assert not names & {
        "DATABASE_URL",
        "PEPPER",
        "TOKEN_PEPPER",
        "KEY_HASH_SECRET",
        "QRYPT_API_KEY",
        "OPENROUTER_API_KEY",
    }
    assert "must-not-reach-the-child" not in env.values()


async def test_agent_echoed_value_never_reaches_the_model(tmp_path):
    log_file = tmp_path / "agent.log"
    configure_logging("DEBUG", str(log_file))
    try:
        agent, model = reset_code_demo()
        await agent.turn(REQUEST)
        model._steps.append(ModelReply("Yes, {{v1}} is valid for 10 minutes."))
        reply = await agent.turn("did 493021 expire? the code 493021 is mine")
    finally:
        configure_logging()
    assert reply == "Yes, 493021 is valid for 10 minutes."
    assert "493021" not in json.dumps(model.requests)
    assert "did {{v1}} expire?" in json.dumps(model.requests)
    assert "493021" not in log_file.read_text()


def test_agent_settings_hold_none_of_the_server_secrets(tmp_path, monkeypatch):
    secrets = {"DATABASE_URL": "postgresql://u:dbpass@h/db", "PEPPER": "pep-secret", "KEY_HASH_SECRET": "hash-secret",
               "QRYPT_API_KEY": "qrypt-secret"}  # fmt: skip
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in secrets.items()) + "MCP_CLIENT_KEY=ck\n")
    monkeypatch.chdir(tmp_path)
    for name in secrets:
        monkeypatch.setenv(name, secrets[name])
    settings = AgentSettings()
    dumped = json.dumps(settings.model_dump(), default=str) + repr(vars(settings))
    assert not any(value in dumped for value in secrets.values())
    assert settings.mcp_client_key == "ck"


def test_agent_cannot_import_the_server_config(tmp_path):
    pkg = tmp_path / "qrypt_entropy" / "agent"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text("from qrypt_entropy.config import get_settings\n")
    assert find_violations(tmp_path) == ["qrypt_entropy/agent/bad.py:1: agent imports config"]
