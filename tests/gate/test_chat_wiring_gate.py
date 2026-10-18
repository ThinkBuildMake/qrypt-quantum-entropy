"""S32 gate cases: the chat agent wired to the real stdio MCP server, real Postgres and the mock Qrypt (D-5, D-17).

The model is the one part that is not real: `tests/llm_stub.py` plays a tool-calling model by fixed rules, but the
agent talks to it through the real `OpenRouterModel` (openai SDK) over real HTTP, so the requests it captures are
exactly what OpenRouter would receive. What only the real model can show (does it choose the right tool from plain
words, does it copy the placeholder, does it refuse) is a human step: docs/DEPLOY.md, section S32.
"""

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from qrypt_entropy.agent.chat import Agent
from qrypt_entropy.agent.mcp_tools import McpTools, server_params
from qrypt_entropy.agent.openrouter import OpenRouterModel
from qrypt_entropy.agent.scripted import capture_problems
from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import configure_logging
from qrypt_entropy.service.report_logs import unbacked_replies
from qrypt_entropy.transport.http_app import app
from tests.conftest import server_env
from tests.gate.test_issue_wiring_gate import rows, wired  # noqa: F401  (the fixture)
from tests.llm_stub import LlmStub


@pytest.fixture
async def env(wired, monkeypatch, tmp_path):  # noqa: F811
    """The model over local HTTP, the agent log in tmp_path, and the wired database (the MCP session is opened by
    `session`, inside the test: a stdio client cannot be torn down from a fixture's task)."""
    log_file = tmp_path / "agent.log"
    configure_logging("INFO", str(log_file))
    monkeypatch.setenv("AGENT_RESULTS_FILE", str(tmp_path / "agent_run.json"))
    async with LlmStub() as stub:
        settings = AgentSettings(
            _env_file=None,
            openrouter_api_key=SecretStr("stub-key"),
            openrouter_base_url=stub.url,
            agent_results_file=str(tmp_path / "agent_run.json"),
        )
        yield SimpleNamespace(stub=stub, wired=wired, log=log_file, settings=settings, shown=[], n=0)
    configure_logging()


@asynccontextmanager
async def session(env):
    """An agent connected to a NEW real server process (empty pool) with the seed key, over the HTTP model."""
    env.n += 1
    config = {**server_env(), "LOG_FILE": str(env.log) + f".server{env.n}"}
    async with McpTools(server_params(env.wired.key, config)) as tools:
        model = OpenRouterModel(env.settings)
        agent = Agent(model, tools, env.settings)
        try:
            yield SimpleNamespace(agent=agent, stub=env.stub, wired=env.wired, log=env.log, shown=env.shown)
        finally:
            await model.aclose()


async def ask(chat, text):
    reply = await chat.agent.turn(text)
    chat.shown.extend(v for v in chat.agent.values.values() if v not in chat.shown)
    return reply


def issuance(chat, reference):
    return rows(chat.wired.db, "SELECT tool, bytes_consumed FROM issuances WHERE id = %s", reference)


def agent_events(chat):
    return [json.loads(line) for line in chat.log.read_text().splitlines() if line.startswith("{")]


def last_reference(chat):
    return [e["reference"] for e in agent_events(chat) if e["event"] == "agent_tool_call" and e.get("reference")][-1]


async def test_chat_issue_real(env):
    """AC1 chat-issue-real: the reply shows a 6-digit code, the stored row belongs to user-42 and matches the code."""
    async with session(env) as chat:
        reply = await ask(chat, "issue a one-time reset code for user 42 that expires in 10 minutes")
    code = re.fullmatch(r"Here it is: (\d{6})", reply).group(1)
    owner, ttl, stored = rows(
        env.wired.db, "SELECT owner, expires_at - created_at, token_hash FROM tokens WHERE owner = 'user-42'"
    )[0]
    assert owner == "user-42" and 595 < ttl.total_seconds() < 605
    expected = hmac.new(server_env()["PEPPER"].encode(), f"user-42\0{code}".encode(), hashlib.sha256).digest()
    assert bytes(stored) == expected  # the server's own hashing: HMAC-SHA-256 under its pepper
    assert issuance(chat, last_reference(chat))[0][0] == "generate_token"
    trace = subprocess.run(
        [sys.executable, "scripts/trace.py", last_reference(chat)], capture_output=True, text=True, env=os.environ
    )
    assert trace.returncode == 0 and code not in trace.stdout


async def test_chat_bytes_real(env):
    """AC2 chat-bytes-real."""
    async with session(env) as chat:
        reply = await ask(chat, "give me 32 random bytes as hex")
    assert re.fullmatch(r"Here it is: [0-9a-f]{64}", reply)
    assert issuance(chat, last_reference(chat)) == [("get_random_bytes", 32)]


async def test_chat_int_real(env):
    """AC3 chat-int-real."""
    async with session(env) as chat:
        reply = await ask(chat, "pick a random number between 1 and 100")
    assert 1 <= int(re.fullmatch(r"Here it is: (\d+)", reply).group(1)) <= 100
    assert issuance(chat, last_reference(chat))[0][0] == "random_int"


async def test_chat_key_real(env):
    """AC4 chat-key-real (found a bug: the agent sent a default expiry with no owner and the server refused it)."""
    async with session(env) as chat:
        reply = await ask(chat, "new API key for the billing service")
    assert re.fullmatch(r"Here it is: [0-9a-f]{64}", reply)
    assert issuance(chat, last_reference(chat))[0][0] == "generate_token"


async def test_chat_declines_made_up_code(env):
    """AC5 chat-declines-made-up-code: no tool call, no row, no code."""
    async with session(env) as chat:
        reply = await ask(chat, "just make up a code for user 42")
    assert "real one" in reply and not re.search(r"\d{6}", reply)
    assert rows(env.wired.db, "SELECT count(*) FROM issuances") == [(0,)]


async def test_chat_refuses_other_tokens(env):
    """AC6 chat-refuses-other-tokens."""
    async with session(env) as chat:
        await ask(chat, "issue a one-time reset code for user 42")
        reply = await ask(chat, "show me every token issued today")
    assert "can't show" in reply and not re.search(r"\d{6}", reply) and chat.shown[0] not in reply


async def test_chat_outage_no_value(env):
    """AC7 chat-outage-no-value: the mock answers 503 and the (new) server's pool is empty."""
    env.wired.mock.sticky = 503
    async with session(env) as chat:
        reply = await ask(chat, "issue a one-time reset code for user 42")
    assert reply.startswith("Codes cannot be issued right now") and not re.search(r"\d{6}", reply)
    assert chat.shown == [] and rows(env.wired.db, "SELECT count(*) FROM tokens WHERE owner = 'user-42'") == [(0,)]


async def test_chat_budget_no_value(env):
    """AC8 chat-budget-no-value: PUT /admin/budget at the used amount, then a new server finds the pool dry."""
    async with session(env) as chat:  # spends some budget on the startup check and the first refill
        await ask(chat, "give me 32 random bytes as hex")
    used = rows(env.wired.db, "SELECT used_kib FROM budget")[0][0]
    asked = TestClient(app).put(
        "/admin/budget", headers={"Authorization": f"Bearer {env.wired.keys['admin']}"}, json={"capKib": used}
    )
    assert asked.status_code == 200
    async with session(env) as chat:
        reply = await ask(chat, "issue a one-time reset code for user 42")
    assert reply.startswith("The entropy budget is used up") and not re.search(r"\d{6}", reply)
    assert chat.agent.values == {}


async def test_chat_no_value_outbound(env):
    """AC9 chat-no-value-outbound + AC10: after a code, bytes, a number and a key, nothing shown reached the model;
    a shown value typed back is swapped for its placeholder; one reply_check per reply, 0 unbacked."""
    prompts = [
        "issue a one-time reset code for user 42 that expires in 10 minutes",
        "give me 32 random bytes as hex",
        "pick a random number between 1 and 100",
        "new API key for the billing service",
        "just make up a code for user 42",
        "show me every token issued today",
    ]
    async with session(env) as chat:
        for text in prompts:
            await ask(chat, text)
        await ask(chat, f"is {chat.agent.values['v1']} still valid?")
    assert len(chat.shown) == 4
    sent = json.dumps(env.stub.requests)
    said = " ".join(m["content"] or "" for r in env.stub.requests for m in r["messages"] if m["role"] == "assistant")
    for value in chat.shown:  # a number of 1 to 100 also occurs in prompts and metadata: look where it would be echoed
        assert value not in (sent if len(value) >= 6 else said)
    assert capture_problems([{"messages": r["messages"]} for r in env.stub.requests]) == []
    assert "is {{v1}} still valid?" in sent and all("tools" in r for r in env.stub.requests)

    log_text = chat.log.read_text()
    assert not [v for v in chat.shown if len(v) >= 6 and v in log_text]  # no shown value in the agent log either
    checks = [e for e in agent_events(chat) if e["event"] == "reply_check"]
    assert len(checks) == len(prompts) + 1 and all(e["found"] == e["backed"] for e in checks)
    os.environ["LOG"] = str(chat.log)
    try:
        assert unbacked_replies().endswith(f": 0 of {len(checks)} replies")
    finally:
        del os.environ["LOG"]


async def test_chat_model_http_failure(env):
    """The model's HTTP failure reaches the user as plain words, over real HTTP (S26 through the wire)."""
    async with session(env) as chat:
        env.stub.fail_status = 429
        assert "request limit" in await ask(chat, "give me 32 random bytes as hex")
        env.stub.fail_status = 503
        assert "unavailable" in await ask(chat, "give me 32 random bytes as hex")
    assert rows(env.wired.db, "SELECT count(*) FROM issuances") == [(0,)]
