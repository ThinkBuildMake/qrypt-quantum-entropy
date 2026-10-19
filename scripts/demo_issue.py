"""`make demo-issue`: the S20 human-demo sequence on a fresh local database against the local mock Qrypt.

Run it through make (it resets the database first). It starts the mock Qrypt and the HTTP service as child processes,
seeds the keys with scripts/seed.py, drives the real MCP server over stdio like MCP Inspector does, and prints the
evidence queries after each step. It needs no Qrypt token. Exit code 1 if any check fails.

AC9 (the live Qrypt run) is the one step it cannot do: it prints the same two queries against the mock, and
docs/DEPLOY.md has the live procedure.
"""

import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import psycopg
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
LOG = "logs/server.log"
MOCK_TOKEN = "demo-mock-token-" + secrets.token_hex(4)  # noqa: S105  (the mock's, not a real credential)
FAILED: list[str] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step(title: str) -> None:
    print(f"\n=== {title}")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def query(sql: str, *args) -> list[tuple]:
    """An evidence query, run as the migrator (the database owner) and printed like psql would."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.execute(sql, args)
        rows = cur.fetchall() if cur.description else []
        header = [d.name for d in cur.description or []]
    print(f"  psql> {sql % tuple(repr(a) for a in args)}")
    for line in [header, *rows]:
        print("    " + " | ".join(str(v) for v in line))
    return rows


def server_params(key: str, quiet: bool = True) -> StdioServerParameters:
    env = {k: os.environ[k] for k in os.environ if k in _PASS} | {"MCP_CLIENT_KEY": key, "LOG_FILE": LOG}
    command = [sys.executable, "-m", "qrypt_entropy.transport.mcp_server"]
    if quiet:  # the JSON log lines still go to LOG_FILE; only the terminal copy is silenced
        return StdioServerParameters(command="/bin/sh", args=["-c", 'exec "$@" 2>/dev/null', "sh", *command], env=env)
    return StdioServerParameters(command=command[0], args=command[1:], env=env)


_PASS = {"PATH", "DATABASE_URL", "KEY_HASH_SECRET", "PEPPER", "TOKEN_PEPPER", "QRYPT_BASE_URL", "QRYPT_API_KEY"}


async def call(client: Client, tool: str, args: dict):
    started = time.perf_counter()
    result = await client.call_tool(tool, args)
    ms = (time.perf_counter() - started) * 1000
    data = json.loads(result.content[0].text) if result.is_error else result.structured_content
    return result.is_error, data, ms


async def warm_up(seconds: float = 2.0) -> None:
    await asyncio.sleep(seconds)  # the first refill (64 KiB) lands well inside this


@contextmanager
def child(args: list[str], ready: str | None = None):
    proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)  # noqa: S603
    try:
        if ready:
            assert ready in (proc.stdout.readline() or ""), f"{args[:3]} did not start"
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def main() -> int:
    os.environ.setdefault("KEY_HASH_SECRET", "demo-" + secrets.token_hex(16))  # demo-only secrets, never reused
    pepper = os.environ.get("PEPPER") or os.environ.get("TOKEN_PEPPER") or "demo-" + secrets.token_hex(16)
    os.environ["PEPPER"] = os.environ["TOKEN_PEPPER"] = pepper
    mock_port, http_port = free_port(), free_port()
    os.environ["QRYPT_BASE_URL"] = f"http://127.0.0.1:{mock_port}"
    os.environ["QRYPT_API_KEY"] = MOCK_TOKEN
    Path(LOG).unlink(missing_ok=True)
    mock = [sys.executable, "-m", "tests.mock_qrypt", "--port", str(mock_port), "--token", MOCK_TOKEN]
    http = [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(http_port)]
    with child(mock, ready="listening"), child(http):
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{http_port}/health")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        return await run(f"http://127.0.0.1:{http_port}", f"http://127.0.0.1:{mock_port}")


async def run(base: str, mock: str) -> int:
    step("Seed (make seed): the admin and two clients; their keys are printed once")
    seeded = subprocess.run([sys.executable, "scripts/seed.py"], capture_output=True, text=True, check=True, cwd=ROOT)  # noqa: S603
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded.stdout))
    print("  " + re.sub(r"key=\S+", "key=<hidden>", seeded.stdout).strip().replace("\n", "\n  "))
    admin = {"Authorization": f"Bearer {keys['admin']}"}

    step("AC3 The hosted admin routes check real keys: GET /admin/usage with the admin key, a client key, no key")
    codes = [
        httpx.get(f"{base}/admin/usage", headers=admin),
        httpx.get(f"{base}/admin/usage", headers={"Authorization": f"Bearer {keys['billing-agent']}"}),
        httpx.get(f"{base}/admin/usage"),
    ]
    print("  " + ", ".join(f"{r.status_code} {r.json().get('error', 'ok')}" for r in codes))
    check("200, 403 forbidden, 401 unauthorized", [r.status_code for r in codes] == [200, 403, 401])

    step("AC1 The seed key connects (billing-agent); the tools are listed")
    async with Client(server_params(keys["billing-agent"])) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        print(f"  tools: {tools}")
        check("three tools listed", sorted(tools) == ["generate_token", "get_random_bytes", "random_int"])
        await warm_up()

        step("AC4 get_random_bytes length 16, hex")
        failed, data, ms = await call(client, "get_random_bytes", {"length": 16, "encoding": "hex"})
        print(f"  result ({ms:.1f} ms): {json.dumps(data)}")
        ref, batch = data["metadata"]["reference"], data["metadata"]["batch_id"]
        meta = data["metadata"]
        check(
            "32 hex characters, source qrypt, 16 bytes consumed",
            bool(re.fullmatch(r"[0-9a-f]{32}", data["value"]))
            and meta["source"] == "qrypt"
            and meta["bytes_consumed"] == 16,
        )
        rows = query("SELECT b.kib, b.outcome FROM batches b WHERE b.id = %s", batch)
        check("batch is 64 KiB or more and received", rows[0][0] >= 64 and rows[0][1] == "received")
        rows = query("SELECT c.name FROM issuances i JOIN credentials c ON c.id = i.client_id WHERE i.id = %s", ref)
        check("AC1: the issuance names billing-agent", rows == [("billing-agent",)])

        step("AC9 (local equivalent, against the mock) the startup check and the first refill, and the meter")
        rows = query("SELECT purpose, kib, outcome FROM batches ORDER BY requested_at")
        check(
            "one startup_check of 1 KiB and one refill of 64 KiB",
            [r[:2] for r in rows] == [("startup_check", 1), ("refill", 64)],
        )
        used = query("SELECT used_kib FROM budget")
        check("used_kib is 1,024 or less", used[0][0] <= 1024)
        print("  (the live run against Qrypt is a human step: docs/DEPLOY.md)")

        step("AC5 random_int 1..6")
        failed, data, ms = await call(client, "random_int", {"min": 1, "max": 6})
        print(f"  result ({ms:.1f} ms): {json.dumps(data)}")
        ref = data["metadata"]["reference"]
        check("an integer from 1 to 6", isinstance(data["value"], int) and 1 <= data["value"] <= 6)
        rows = query(
            "SELECT i.tool, i.bytes_consumed, (SELECT sum(byte_count) FROM issuance_batches WHERE issuance_id = i.id)"
            " FROM issuances i WHERE i.id = %s",
            ref,
        )
        check(
            "tool random_int, byte_count values sum to bytes_consumed",
            rows[0][0] == "random_int" and rows[0][1] == rows[0][2],
        )

        step("AC6 generate_token otp_6 for user-42, 600 s")
        args = {"kind": "otp_6", "owner": "user-42", "expiry_seconds": 600}
        failed, data, ms = await call(client, "generate_token", args)
        print(f"  result ({ms:.1f} ms): {json.dumps(data)}")
        check("6 digits", bool(re.fullmatch(r"\d{6}", data["value"])))
        rows = query(
            "SELECT owner, issuance_id, encode(token_hash, 'hex') AS token_hash FROM tokens WHERE owner = 'user-42'"
        )
        check(
            "a hash is stored, tied to the issuance",
            str(rows[0][1]) == data["metadata"]["reference"] and data["value"] not in rows[0][2],
        )

        step("AC8 get_random_bytes length 0")
        failed, data, ms = await call(client, "get_random_bytes", {"length": 0, "encoding": "hex"})
        print(f"  result: {json.dumps(data)}")
        check(
            "invalid_argument naming length",
            failed and data["error"] == "invalid_argument" and "length" in data["message"],
        )

    step("AC2 A revoked key is refused (a throwaway client, created then revoked through the admin routes)")
    made = httpx.post(f"{base}/admin/clients", headers=admin, json={"name": "throwaway"}).json()
    httpx.post(f"{base}/admin/clients/{made['client']['id']}/revoke", headers=admin)
    refused = False
    try:
        async with Client(server_params(made["key"])) as client:
            await client.list_tools()
    except BaseException as exc:  # noqa: BLE001  (a refused key closes the connection)
        refused = not isinstance(exc, KeyboardInterrupt)
    params = server_params(made["key"], quiet=False)
    ran = subprocess.run(  # noqa: S603
        [params.command, *params.args],
        env=params.env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    event = json.loads(ran.stderr.strip().splitlines()[-1])
    print(f"  the server's last line: {json.dumps(event)}")
    refused = refused and ran.returncode == 1 and ran.stdout == "" and event["event"] == "connect_refused"
    refused = refused and event["error"] == "unauthorized" and "revoked" in event["cause"]
    check("the connection is refused with unauthorized; no tool listed", refused)

    step("AC7 A refused token insert fails the whole call (a trigger on tokens refuses every insert)")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "CREATE FUNCTION refuse_token() RETURNS trigger LANGUAGE plpgsql"
            " AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$"
        )
        conn.execute("CREATE TRIGGER refuse BEFORE INSERT ON tokens FOR EACH ROW EXECUTE FUNCTION refuse_token()")
    before = query("SELECT count(*) AS issuances FROM issuances")[0][0]
    try:
        async with Client(server_params(keys["billing-agent"])) as client:
            await warm_up(1.0)
            failed, data, ms = await call(client, "generate_token", {"kind": "otp_6", "owner": "user-43"})
    finally:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            conn.execute("DROP TRIGGER refuse ON tokens")
            conn.execute("DROP FUNCTION refuse_token()")
    print(f"  result: {json.dumps(data)}")
    after = query("SELECT count(*) AS issuances FROM issuances")[0][0]
    check(
        "temporarily_unavailable, no value, no new issuance row",
        failed and data["error"] == "temporarily_unavailable" and after == before,
    )

    step("Latency: 1,000 get_random_bytes calls on a warm pool (the p95 must stay under 50 ms; PRD)")
    durations = []
    async with Client(server_params(keys["billing-agent"])) as client:
        await warm_up()
        for _ in range(1000):
            durations.append((await call(client, "get_random_bytes", {"length": 16, "encoding": "hex"}))[2])
    durations.sort()
    p95 = durations[949]
    print(f"  p50 {durations[499]:.1f} ms, p95 {p95:.1f} ms, p99 {durations[989]:.1f} ms, max {durations[-1]:.1f} ms")
    check("p95 under 50 ms (client side, through stdio)", p95 < 50)

    step("AC10 Qrypt down (the mock answers 503), pool drained: a fresh server session, then a call")
    httpx.get(f"{mock}/__mock/status", params={"code": 503})
    try:
        before = query("SELECT count(*) AS issuances FROM issuances")[0][0]
        async with Client(server_params(keys["billing-agent"])) as client:
            failed, data, ms = await call(client, "get_random_bytes", {"length": 16, "encoding": "hex"})
        print(f"  result after {ms / 1000:.2f} s: {json.dumps(data)}")
        after = query("SELECT count(*) AS issuances FROM issuances")[0][0]
        check(
            "temporarily_unavailable within 2 s, no value, no new row",
            failed and data["error"] == "temporarily_unavailable" and ms < 2000 and after == before,
        )
    finally:
        httpx.get(f"{mock}/__mock/status", params={"code": 200})

    step("AC11 make report LOG=logs/server.log")
    # what the make target runs (a nested make would see the UV variable that `uv run` exports)
    report = [sys.executable, "-m", "qrypt_entropy.transport.report_cli"]
    done = subprocess.run(report, capture_output=True, text=True, cwd=ROOT, env={**os.environ, "LOG": LOG})  # noqa: S603
    print("  " + "\n  ".join(done.stdout.splitlines() or done.stderr.splitlines()[-5:]))
    panels = {int(n): reading for n, reading in re.findall(r"^PANEL (\d+) .*?: (.*)$", done.stdout, re.M)}
    check(
        "p95, KiB per request, use against the cap, time to unavailable show numbers",
        all(panels.get(n, "no data") != "no data" for n in (4, 7, 8, 9)),
    )

    print("\n" + ("DEMO FAILED: " + "; ".join(FAILED) if FAILED else "DEMO PASSED"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
