"""`make demo-operate`: the S34 human-demo sequence on a fresh local database against the local mock Qrypt.

Run it through make (it resets the database first). It starts the mock Qrypt and the HTTP service as child processes,
seeds the keys with scripts/seed.py, drives the admin routes like curl and the real MCP server over stdio like MCP
Inspector, and prints the evidence queries after each step. It needs no Qrypt token. Exit code 1 if any check fails.
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
from demo_issue import FAILED, LOG, MOCK_TOKEN, ROOT, call, check, child, free_port, query, server_params, step
from mcp.client import Client

HEX = {"length": 1024, "encoding": "hex"}


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
        return await run(f"http://127.0.0.1:{http_port}")


def show(label: str, response: httpx.Response) -> dict:
    print(f"  curl {label} -> {response.status_code} {response.text.strip()[:300]}")
    return response.json()


async def run(base: str) -> int:
    step("make seed: the admin and two clients; their keys are printed once")
    seeded = subprocess.run([sys.executable, "scripts/seed.py"], capture_output=True, text=True, check=True, cwd=ROOT)  # noqa: S603
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded.stdout))
    print("  " + re.sub(r"key=\S+", "key=<hidden>", seeded.stdout).strip().replace("\n", "\n  "))
    admin = {"Authorization": f"Bearer {keys['admin']}"}

    step("AC1 An empty system: GET /admin/usage with the seed admin key")
    view = show("GET /admin/usage", httpx.get(f"{base}/admin/usage", headers=admin))
    check(
        "capKib 10240, usedKib 0, perClient empty", view == {"budget": {"capKib": 10240, "usedKib": 0}, "perClient": []}
    )

    step("AC2 POST /admin/clients creates reporting-agent; its key connects and the tools are listed")
    made = httpx.post(f"{base}/admin/clients", headers=admin, json={"name": "reporting-agent"}).json()
    print(f"  curl POST /admin/clients -> client {made['client']}, key <hidden>")
    reporting_id, reporting_key = made["client"]["id"], made["key"]
    async with Client(server_params(reporting_key)) as client:
        tools = sorted(t.name for t in (await client.list_tools()).tools)
        print(f"  tools: {tools}")
        check("three tools listed", tools == ["generate_token", "get_random_bytes", "random_int"])

    step("AC3 Usage per client: billing-agent and reporting-agent each make calls")
    consumed = {}
    for name, key in (("billing-agent", keys["billing-agent"]), ("reporting-agent", reporting_key)):
        total = 0
        async with Client(server_params(key)) as client:
            for tool, args in (
                ("get_random_bytes", {"length": 16, "encoding": "hex"}),
                ("random_int", {"min": 1, "max": 6}),
            ):
                _, data, _ = await call(client, tool, args)
                total += data["metadata"]["bytes_consumed"]
        consumed[name] = total
    print(f"  bytes_consumed summed from the tool metadata: {consumed}")
    view = show("GET /admin/usage", httpx.get(f"{base}/admin/usage", headers=admin))
    rows = query("SELECT sum(kib) AS sum_kib FROM batches")
    check(
        "perClient equals the metadata sums and usedKib equals sum(kib) of batches",
        {c["name"]: c["bytesConsumed"] for c in view["perClient"]} == consumed
        and view["budget"]["usedKib"] == rows[0][0],
    )

    step("AC4 POST /admin/clients/{id}/revoke on reporting-agent; its key is refused")
    show("POST revoke", httpx.post(f"{base}/admin/clients/{reporting_id}/revoke", headers=admin))
    refused = False
    try:
        async with Client(server_params(reporting_key)) as client:
            await client.list_tools()
    except BaseException as exc:  # noqa: BLE001  (a refused key closes the connection)
        refused = not isinstance(exc, KeyboardInterrupt)
    last = Path(LOG).read_text().strip().splitlines()[-1]
    print(f"  the server's last log line: {last}")
    check("connection refused with unauthorized", refused and '"connect_refused"' in last and "unauthorized" in last)

    step("AC5 An open session for support-agent, then support-agent is revoked: the next call is refused")
    support_id = query("SELECT id::text FROM credentials WHERE name = 'support-agent'")[0][0]
    async with Client(server_params(keys["support-agent"])) as client:
        failed, data, _ = await call(client, "get_random_bytes", HEX)
        check("the open session works before the revocation", not failed)
        before = query("SELECT used_kib, (SELECT count(*) FROM issuances) AS issuances FROM budget")
        show("POST revoke", httpx.post(f"{base}/admin/clients/{support_id}/revoke", headers=admin))
        failed, data, _ = await call(client, "get_random_bytes", HEX)
        print(f"  result: {data}")
        after = query("SELECT used_kib, (SELECT count(*) FROM issuances) AS issuances FROM budget")
        check(
            "unauthorized, no value, used_kib and issuances unchanged",
            failed and data["error"] == "unauthorized" and before == after,
        )

    step("AC6 PUT /admin/budget leaving 100 KiB of room, then get_random_bytes (1,024 bytes) until it is spent")
    used_now = query("SELECT used_kib FROM budget")[0][0]
    cap = used_now + 100  # use only rises, so "100 KiB" here is 100 KiB more than has been used so far
    print(f"  {used_now} KiB used so far; the cap is set to {cap} (the integration case runs 100 from empty)")
    show("PUT /admin/budget", httpx.put(f"{base}/admin/budget", headers=admin, json={"capKib": cap}))
    results = []
    async with Client(server_params(keys["billing-agent"])) as client:
        for _ in range(110):
            results.append(await call(client, "get_random_bytes", HEX))
    served = sum(1 for failed, *_ in results if not failed)
    last_error = next(data for failed, data, _ in reversed(results) if failed)
    print(f"  {served} calls served, {len(results) - served} refused; last refusal: {last_error}")
    used = query("SELECT used_kib, cap_kib FROM budget")[0]
    check(
        "budget_exhausted with no value, used_kib never past the cap",
        last_error["error"] == "budget_exhausted" and "value" not in last_error and used == (cap, cap),
    )
    check("refusals only after the last success", [f for f, *_ in results] == sorted(f for f, *_ in results))
    alerts = [json.loads(line) for line in Path(LOG).read_text().splitlines() if "budget_alert" in line]
    print("  grep budget logs/server.log:\n    " + "\n    ".join(json.dumps(a) for a in alerts))
    check("the 80% and 100% alerts are each logged once", [a["threshold_percent"] for a in alerts] == [80, 100])

    step("AC7 make report: the Qrypt-use-against-the-cap panel")
    report = [sys.executable, "-m", "qrypt_entropy.transport.report_cli"]
    done = subprocess.run(report, capture_output=True, text=True, cwd=ROOT)  # noqa: S603
    panel = next((line for line in done.stdout.splitlines() if "Qrypt use against the cap" in line), "")
    print("  " + panel)
    check("shows the KiB used and marks the 80% level", f"{cap} of {cap} KiB" in panel and "at or above the" in panel)

    print("\n" + ("DEMO FAILED: " + "; ".join(FAILED) if FAILED else "DEMO PASSED"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
