"""`make demo-chat`: the S32 chat sequence on a fresh local database against the local mock Qrypt.

Run it through make (it resets the database first). The agent is real: the real stdio MCP server, real Postgres, the
mock Qrypt, and `OpenRouterModel` (the openai SDK) over real HTTP. The one stand-in is the MODEL: `tests/llm_stub.py`
is a tool-calling stub that picks a tool by matching phrases and repeats the placeholder it is shown. It is not a
language model, so this demo proves the wiring and the guarantees the agent enforces (CP-5, plain failures, the reply
check); it cannot show how a real model understands plain words or whether it ever invents a code. That run is a human
step: docs/DEPLOY.md, section S32. Exit code 1 if any check fails.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import demo_issue as d  # (scripts/ is on sys.path) child processes, queries, checks, MCP launch
import httpx
from pydantic import SecretStr

from qrypt_entropy.agent.chat import Agent
from qrypt_entropy.agent.mcp_tools import McpTools
from qrypt_entropy.agent.openrouter import OpenRouterModel
from qrypt_entropy.agent.scripted import capture_problems
from qrypt_entropy.agent.settings import AgentSettings
from qrypt_entropy.logs import configure_logging
from tests.llm_stub import LlmStub

AGENT_LOG = "logs/agent.log"
CAPTURE = "logs/agent-capture.json"
SESSION = [
    ("AC1", "issue a one-time reset code for user 42 that expires in 10 minutes"),
    ("AC2", "give me 32 random bytes as hex"),
    ("AC3", "pick a random number between 1 and 100"),
    ("AC4", "new API key for the billing service"),
    ("AC5", "just make up a code for user 42"),
    ("AC6", "show me every token issued today"),
]


def settings_for(stub: LlmStub) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        openrouter_api_key=SecretStr("demo-stub-key"),
        openrouter_base_url=stub.url,
        agent_results_file="logs/agent_run.json",
    )


async def fresh_turn(key: str, stub: LlmStub, text: str) -> str:
    """A new server process (an empty pool) and a new conversation: one request, one reply."""
    async with McpTools(d.server_params(key)) as tools:
        model = OpenRouterModel(settings_for(stub))
        agent = Agent(model, tools, settings_for(stub))
        reply = await agent.turn(text)
        await model.aclose()
    assert not agent.values, "a value was issued"
    return reply


def report_panel() -> str:
    """The unbacked-replies panel line of `make report LOG=logs/agent.log`."""
    report = [sys.executable, "-m", "qrypt_entropy.transport.report_cli"]
    done = subprocess.run(report, capture_output=True, text=True, cwd=d.ROOT, env={**os.environ, "LOG": AGENT_LOG})  # noqa: S603
    return next((x for x in done.stdout.splitlines() if "unbacked" in x), done.stderr[-300:])


async def run(base: str, mock: str) -> int:
    step, check = d.step, d.check
    step("Seed (make seed)")
    seeded = subprocess.run([sys.executable, "scripts/seed.py"], capture_output=True, text=True, check=True, cwd=d.ROOT)  # noqa: S603
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded.stdout))
    client, admin = keys["billing-agent"], {"Authorization": f"Bearer {keys['admin']}"}

    Path(AGENT_LOG).unlink(missing_ok=True)
    configure_logging("INFO", AGENT_LOG)
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if type(h) is not logging.StreamHandler]  # the log file only

    async with LlmStub() as stub:
        step("The session: the agent starts the real MCP server; the model is the local stub over HTTP")
        shown, replies = [], []
        async with McpTools(d.server_params(client)) as tools:
            model = OpenRouterModel(settings_for(stub))
            agent = Agent(model, tools, settings_for(stub))
            for label, text in SESSION:
                reply = await agent.turn(text)
                replies.append(reply)
                shown = list(agent.values.values())
                print(f"  {label} you> {text}\n       agent> {reply}")
            await model.aclose()
        code, hex32, number, key = shown[:4]

        step("AC1 evidence: the stored row, the hash match, the trace of the reference in the agent log")
        rows = d.query("SELECT owner, expires_at - created_at AS expires_in FROM tokens")
        mine = [r for r in rows if r[0] == "user-42"]
        check(
            "one row for user-42 expiring in about 10 minutes",
            len(mine) == 1 and 595 < mine[0][1].total_seconds() < 605,
        )
        log_events = [json.loads(x) for x in Path(AGENT_LOG).read_text().splitlines() if x.startswith("{")]
        refs = [e["reference"] for e in log_events if e["event"] == "agent_tool_call" and e.get("reference")]
        stored = d.query("SELECT encode(token_hash, 'hex') FROM tokens WHERE owner = 'user-42'")[0][0]
        expected = hmac.new(os.environ["PEPPER"].encode(), f"user-42\0{code}".encode(), hashlib.sha256).hexdigest()
        check("the code shown matches the stored hash", stored == expected)
        trace = subprocess.run(
            [sys.executable, "scripts/trace.py", refs[0]], capture_output=True, text=True, cwd=d.ROOT
        )  # noqa: S603
        print("  make trace REF=" + refs[0] + "\n    " + trace.stdout.strip().replace("\n", "\n    ")[:700])
        check("trace answers for the reference in the agent log", trace.returncode == 0 and code not in trace.stdout)

        step("AC2-AC4 evidence: each reference in the agent log has its issuance row")
        audit = d.query("SELECT tool, bytes_consumed FROM issuances ORDER BY tool, bytes_consumed")
        check("a get_random_bytes row of 32 bytes, a random_int row, two generate_token rows",
              {("get_random_bytes", 32)} <= set(audit) and sorted(r[0] for r in audit).count("generate_token") == 2
              and any(r[0] == "random_int" for r in audit))  # fmt: skip
        check("shape of what was shown",
              bool(re.fullmatch(r"\d{6}", code)) and bool(re.fullmatch(r"[0-9a-f]{64}", hex32))
              and 1 <= int(number) <= 100 and bool(re.fullmatch(r"[0-9a-f]{64}", key)))  # fmt: skip
        check("AC5/AC6: declined and refused, no value in either reply",
              "real one" in replies[4] and "can't show" in replies[5]
              and not any(v in replies[4] + replies[5] for v in shown))  # fmt: skip

        step("AC9 (CP-5) no issued value in any request the model received")
        Path(CAPTURE).write_text(json.dumps(stub.requests, indent=2))
        sent = json.dumps(stub.requests)
        said = " ".join(m["content"] or "" for r in stub.requests for m in r["messages"] if m["role"] == "assistant")
        found = [v for v in shown if v in (sent if len(v) >= 6 else said)]  # a 1-100 number occurs by chance elsewhere
        print(f"  {len(stub.requests)} captured requests; searched for {len(shown)} values shown: {len(found)} matches")
        check("0 matches", not found and not capture_problems(stub.requests))
        agent_check = subprocess.run(  # what `make agent-capture-check` runs  # noqa: S603
            [sys.executable, "-m", "qrypt_entropy.agent", "capture-check", CAPTURE],
            capture_output=True,
            text=True,
            cwd=d.ROOT,
        )
        print("  agent-capture-check> " + agent_check.stdout.strip())
        check("agent-capture-check passes", agent_check.returncode == 0)

        step("AC10 make report LOG=logs/agent.log: the unbacked-replies panel, real counts")
        panel = report_panel()
        print("  " + panel)
        checks = [e for e in log_events if e["event"] == "reply_check"]
        zero = re.search(rf": 0 of {len(SESSION)} replies", panel) is not None
        check("one reply_check per reply, 0 replies with an unbacked value", len(checks) == len(SESSION) and zero)

        step("A model reply that invents a number is caught: the stub is told to answer with a made-up code")
        # reply check (S27) with the real agent: the stub's next answer is text holding an invented code
        invented = await invented_reply(client, stub)
        print(f"  agent> {invented}")
        check(
            "the invented value is removed and the user is told",
            "731905" not in invented and "no tool issued it" in invented,
        )

        step("AC7 The Qrypt mock answers 503 and the pool is drained (a fresh server session)")
        httpx.get(f"{mock}/__mock/status", params={"code": 503})
        try:
            reply = await fresh_turn(client, stub, "issue a one-time reset code for user 42")
        finally:
            httpx.get(f"{mock}/__mock/status", params={"code": 200})
        print(f"  agent> {reply}")
        check(
            "plain words, no code",
            reply.startswith("Codes cannot be issued right now") and not re.search(r"\d{6}", reply),
        )

        step("AC8 The cap is set to the amount used with PUT /admin/budget; the pool is drained (a fresh session)")
        used = d.query("SELECT used_kib FROM budget")[0][0]
        put = httpx.put(f"{base}/admin/budget", headers=admin, json={"capKib": used})
        print(f"  curl -X PUT {base}/admin/budget -d '{{\"capKib\": {used}}}' -> {put.status_code}")
        reply = await fresh_turn(client, stub, "issue a one-time reset code for user 42")
        print(f"  agent> {reply}")
        check(
            "budget used up, no code",
            reply.startswith("The entropy budget is used up") and not re.search(r"\d{6}", reply),
        )

        step("The language model itself fails (the stub answers 429, then 503)")
        replies = []
        for status in (429, 503):
            stub.fail_status = status
            replies.append(await fresh_turn(client, stub, "give me 32 random bytes as hex"))
            print(f"  {status} -> agent> {replies[-1]}")
        stub.fail_status = None
        check("plain words for both, no value", "request limit" in replies[0] and "unavailable" in replies[1])

    step("Final report panel, all replies (the 1 unbacked is the invented-number reply the demo staged on purpose)")
    print("  " + report_panel())
    print("\n" + ("DEMO FAILED: " + "; ".join(d.FAILED) if d.FAILED else "DEMO PASSED"))
    print("Not shown here: how a REAL model reads plain words, or whether it invents a value (docs/DEPLOY.md S32).")
    return 1 if d.FAILED else 0


async def invented_reply(client: str, stub: LlmStub) -> str:
    """One turn where the 'model' answers with an invented 6-digit code and no tool call."""
    real = stub._answer

    def answer(body: dict) -> tuple[int, dict]:
        status, payload = real(body)
        payload["choices"][0]["message"] = {"role": "assistant", "content": "Sure, your code is 731905."}
        payload["choices"][0]["finish_reason"] = "stop"
        return status, payload

    stub._answer = answer
    try:
        return await fresh_turn(client, stub, "hi")
    finally:
        stub._answer = real


async def main() -> int:
    os.environ.setdefault("KEY_HASH_SECRET", "demo-" + secrets.token_hex(16))  # demo-only secrets, never reused
    pepper = os.environ.get("PEPPER") or os.environ.get("TOKEN_PEPPER") or "demo-" + secrets.token_hex(16)
    os.environ["PEPPER"] = os.environ["TOKEN_PEPPER"] = pepper
    mock_port, http_port = d.free_port(), d.free_port()
    os.environ["QRYPT_BASE_URL"] = f"http://127.0.0.1:{mock_port}"
    os.environ["QRYPT_API_KEY"] = d.MOCK_TOKEN
    mock = [sys.executable, "-m", "tests.mock_qrypt", "--port", str(mock_port), "--token", d.MOCK_TOKEN]
    http = [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(http_port)]
    with d.child(mock, ready="listening"), d.child(http):
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{http_port}/health")
                break
            except httpx.TransportError:
                await asyncio.sleep(0.1)
        return await run(f"http://127.0.0.1:{http_port}", f"http://127.0.0.1:{mock_port}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
