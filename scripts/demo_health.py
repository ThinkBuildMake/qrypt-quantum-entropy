"""`make demo-health`: the S35 human-demo sequence on a fresh local database against the local mock Qrypt.

Run it through make (it resets the database first). It starts the mock Qrypt and the HTTP service as child processes,
seeds the keys, runs the real MCP server over stdio, and prints what `curl $BASE/health` and `curl $BASE/metrics`
answer while the server runs, stops and restarts. It needs no Qrypt token. Takes about a minute. Exit code 1 if
any check fails.
"""

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from demo_issue import FAILED, LOG, ROOT, call, check, child, free_port, server_params, step
from mcp.client import Client

HEX = {"length": 16, "encoding": "hex"}
SNAPS = "logs/demo-health-snaps.json"
SERIES = ("qrypt_report_age_seconds", "qrypt_pool_level_bytes", "qrypt_calls_total", "qrypt_errors_total")


def health(base: str) -> dict:
    data = httpx.get(f"{base}/health").json()
    print(f"  $ curl -s $BASE/health\n  {json.dumps(data)}")
    return data


def metrics(base: str, key: str | None, show: bool = True) -> httpx.Response:
    r = httpx.get(f"{base}/metrics", headers={"Authorization": f"Bearer {key}"} if key else {})
    if show:
        print(f"  $ curl -s -H 'Authorization: Bearer $ADMIN_KEY' $BASE/metrics   -> {r.status_code}")
        for line in r.text.splitlines():
            if line.startswith(SERIES + ("qrypt_upstream_requests_last_10s", "qrypt_budget_used_kib")):
                print(f"    {line}")
    return r


async def main() -> int:
    os.environ.setdefault("KEY_HASH_SECRET", "demo-" + secrets.token_hex(16))  # demo-only secrets, never reused
    pepper = os.environ.get("PEPPER") or os.environ.get("TOKEN_PEPPER") or "demo-" + secrets.token_hex(16)
    os.environ["PEPPER"] = os.environ["TOKEN_PEPPER"] = pepper
    token = "demo-mock-token-" + secrets.token_hex(4)  # the mock's, not a real credential
    mock_port, http_port = free_port(), free_port()
    os.environ["QRYPT_BASE_URL"] = f"http://127.0.0.1:{mock_port}"
    os.environ["QRYPT_API_KEY"] = token
    for leftover in (LOG, SNAPS):
        Path(leftover).unlink(missing_ok=True)
    mock = [sys.executable, "-m", "tests.mock_qrypt", "--port", str(mock_port), "--token", token]
    http = [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(http_port)]
    with child(mock, ready="listening"), child(http):
        base = f"http://127.0.0.1:{http_port}"
        for _ in range(100):
            try:
                httpx.get(f"{base}/health")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        return await run(base)


async def run(base: str) -> int:
    seeded = subprocess.run([sys.executable, "scripts/seed.py"], capture_output=True, text=True, check=True, cwd=ROOT)  # noqa: S603
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded.stdout))
    admin, client_key = keys["admin"], keys["billing-agent"]

    step("AC1 Health before any server has run (database just reset)")
    check("200 with reporting false", health(base)["reporting"] is False)

    step("AC8 sampler: make sample-metrics SECONDS=40 scrapes /metrics every 5 s while the calls below happen")
    env = {**os.environ, "METRICS_URL": f"{base}/metrics", "ADMIN_KEY": admin, "SECONDS": "40", "SNAPSHOTS": SNAPS}
    sampler = subprocess.Popen([sys.executable, "-m", "qrypt_entropy.transport.metrics_sampler"], env=env, cwd=ROOT)  # noqa: S603

    async with Client(server_params(client_key)) as client:
        step("AC2 The MCP server runs; 10 s later /health shows the pool and the upstream")
        await call(client, "get_random_bytes", HEX)
        await asyncio.sleep(10)
        h = health(base)
        check(
            "reporting, age 6 s or less, marks 32768 and 131072, level above 0, upstream ok",
            h["reporting"]
            and h["reportAgeSeconds"] <= 6
            and (h["pool"]["lowMarkBytes"], h["pool"]["highMarkBytes"]) == (32768, 131072)
            and h["pool"]["levelBytes"] > 0
            and h["upstream"]["status"] == "ok",
        )

        step("AC5 4 more get_random_bytes calls (5 in all) and 1 invalid one; the next report carries them")
        for _ in range(4):
            await call(client, "get_random_bytes", HEX)
        failed, _, _ = await call(client, "get_random_bytes", {"length": 0, "encoding": "hex"})
        await asyncio.sleep(7)
        r = metrics(base, admin)
        check(
            "get_random_bytes 5, invalid_argument 1, pool, upstream-rate, budget and duration series present",
            'qrypt_calls_total{tool="get_random_bytes"} 5.0' in r.text
            and 'qrypt_errors_total{code="invalid_argument"} 1.0' in r.text
            and all(s in r.text for s in ("qrypt_pool_level_bytes", "qrypt_upstream_requests_last_10s"))
            and "qrypt_budget_used_kib" in r.text
            and "qrypt_tool_call_duration_ms" in r.text,
        )

        step("AC6 /metrics with a client key and with no key")
        codes = [metrics(base, client_key, False).status_code, metrics(base, None, False).status_code]
        print(f"  {codes}")
        check("403 then 401", codes == [403, 401])

    step("AC3 The MCP server is stopped; 16 s later /health reads not reporting")
    await asyncio.sleep(16)
    check("reporting false", health(base)["reporting"] is False)

    step("AC7 Stopped for 20 s: /metrics keeps only the report age (and the budget)")
    await asyncio.sleep(4)
    r = metrics(base, admin)
    check(
        "pool and call series absent, report age present",
        "qrypt_report_age_seconds" in r.text
        and "qrypt_pool_level_bytes" not in r.text
        and "qrypt_calls_total" not in r.text,
    )

    step("Restart: a new server process reports under a new instance row")
    async with Client(server_params(client_key)) as client:
        await call(client, "get_random_bytes", HEX)
        await asyncio.sleep(2)
        h = health(base)
    rows = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import psycopg,os;print(psycopg.connect(os.environ['DATABASE_URL']).execute("
         "'select count(distinct instance_id) from server_status').fetchone()[0])"],
        capture_output=True, text=True, cwd=ROOT,
    )  # fmt: skip
    print(f"  distinct instance rows in server_status: {rows.stdout.strip()}")
    check("reporting again, two instance rows", h["reporting"] and rows.stdout.strip() == "2")

    step("AC4 A bad Qrypt token: a server started with the wrong token reports upstream misconfigured")
    bad = server_params(client_key)
    bad = bad.model_copy(update={"env": {**bad.env, "QRYPT_API_KEY": "wrong-token", "LOG_FILE": "logs/demo-bad.log"}})
    Path("logs/demo-bad.log").unlink(missing_ok=True)
    async with Client(bad) as client:
        await client.list_tools()
        await asyncio.sleep(7)
        h = health(base)
    events = [json.loads(x) for x in Path("logs/demo-bad.log").read_text().splitlines() if x.startswith("{")]
    failed_check = next((e for e in events if e.get("event") == "pool_startup_check_failed"), {})
    print(f"  server log event: {json.dumps(failed_check)}")
    check(
        "upstream.status misconfigured and the log names the invalid or expired token",
        h["upstream"]["status"] == "misconfigured" and "expired" in failed_check.get("cause", ""),
    )

    step("AC8 make report SNAPSHOTS=snaps.json (after the sampler's 40 s)")
    sampler.wait(timeout=60)
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"],
        capture_output=True, text=True, cwd=ROOT, env={**os.environ, "SNAPSHOTS": SNAPS},
    )  # fmt: skip
    panels = [x for x in done.stdout.splitlines() if "server-caused errors" in x or "worst 10 s" in x]
    print("  " + "\n  ".join(panels))
    check(
        "server-caused errors 0 and worst-10-seconds is a request count within the limit",
        any("server-caused" in p and ": 0 in" in p for p in panels)
        and any("worst 10 s" in p and re.search(r"requests \(within", p) for p in panels),
    )

    print("\n" + ("DEMO FAILED: " + "; ".join(FAILED) if FAILED else "DEMO PASSED"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
