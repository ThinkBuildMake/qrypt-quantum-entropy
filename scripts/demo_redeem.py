"""`make demo-redeem`: the S33 human-demo sequence on a fresh local database against the local mock Qrypt.

Run it through make (it resets the database first). It starts the mock Qrypt and the HTTP service as child processes,
seeds the keys, issues codes with the real stdio MCP server, then redeems and traces them with the same curl-shaped
HTTP calls a person would type, printing the evidence queries as it goes. It needs no Qrypt token. Exit code 1 if any
check fails.

Residual risk (docs/DEVIATIONS.md, owner lock-out): five wrong tries lock an owner out for the window, so anyone who
knows an owner's name can lock that owner out; step AC5 shows exactly that behaviour.
"""

import asyncio
import os
import re
import secrets
import subprocess
import sys
import time
import uuid

import demo_issue as d  # (scripts/ is on sys.path) the shared helpers: child processes, queries, MCP launch
import httpx
import psycopg

BASE: str = ""


def show(label: str, response: httpx.Response) -> httpx.Response:
    print(f"  {label} -> {response.status_code} {response.text.strip()}")
    return response


async def issue(key: str, expiry: int, owner: str = "user-42") -> tuple[str, dict]:
    async with d.Client(d.server_params(key)) as client:
        await d.warm_up(1.0)
        failed, data, _ = await d.call(
            client, "generate_token", {"kind": "otp_6", "owner": owner, "expiry_seconds": expiry}
        )
    assert not failed, data
    print(f"  issued by the MCP server: owner {owner}, expiry {expiry} s, reference {data['metadata']['reference']}")
    return data["value"], data["metadata"]


def verify(key: str, owner: str, value: str) -> httpx.Response:
    return show(
        f"POST /verify owner={owner} value={value}",
        httpx.post(f"{BASE}/verify", headers={"Authorization": f"Bearer {key}"}, json={"owner": owner, "value": value}),
    )


async def run() -> int:
    step, check = d.step, d.check
    step("Seed (make seed)")
    seeded = subprocess.run([sys.executable, "scripts/seed.py"], capture_output=True, text=True, check=True, cwd=d.ROOT)  # noqa: S603
    keys = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded.stdout))
    client, admin = keys["billing-agent"], keys["admin"]

    step("AC1 A code issued by the MCP server redeems through the HTTP service")
    code, meta = await issue(client, 600)
    ref = meta["reference"]
    r = verify(client, "user-42", code)
    check("200 redeemed", r.status_code == 200 and r.json() == {"outcome": "redeemed"})

    step("AC2 A second redemption is refused")
    r = verify(client, "user-42", code)
    check("409 already_used", r.status_code == 409 and r.json()["error"] == "already_used")

    step("AC3 A redemption after expiry is refused (expiry 5 s, waiting 6 s)")
    short, _ = await issue(
        client, 5, owner="user-43"
    )  # the mock repeats its bytes, so a distinct owner keeps codes apart
    time.sleep(6)
    r = verify(client, "user-43", short)
    check("410 expired", r.status_code == 410 and r.json()["error"] == "expired")

    step("AC4 A wrong code is refused")
    r = verify(client, "user-42", "000000" if code != "000000" else "111111")
    check("404 unknown", r.status_code == 404 and r.json()["error"] == "unknown")

    step("AC5 Five wrong tries lock the owner out (owner user-77, live code; see the lock-out risk in DEVIATIONS)")
    live, _ = await issue(client, 600, owner="user-77")
    wrong = "000000" if live != "000000" else "111111"
    codes = [verify(client, "user-77", wrong).status_code for _ in range(5)]
    r = verify(client, "user-77", live)
    check("five 404s, then 429 rate_limited for the correct code", codes == [404] * 5 and r.status_code == 429)

    step("AC6 An admin traces the issuance to its batch")
    print(f"  curl -s -H 'Authorization: Bearer $ADMIN_KEY' $BASE/admin/issuances/{ref}")
    r = show(
        "GET /admin/issuances/{ref}",
        httpx.get(f"{BASE}/admin/issuances/{ref}", headers={"Authorization": f"Bearer {admin}"}),
    )
    body = r.json()
    batches, token = body.get("batches") or [{}], body.get("token") or {}
    check(
        "client, tool, bytesConsumed, batch (qrypt, byteOffset), token (user-42, otp_6, usedAt), no code",
        body.get("client", {}).get("name") == "billing-agent"
        and body["tool"] == "generate_token"
        and body["bytesConsumed"] > 0
        and batches[0].get("source") == "qrypt"
        and "byteOffset" in batches[0]
        and (token.get("owner"), token.get("kind")) == ("user-42", "otp_6")
        and token.get("usedAt")
        and code not in r.text,
    )

    step("AC7 Unknown reference and the wrong key are refused")
    a = show(
        "GET unknown uuid, admin key",
        httpx.get(f"{BASE}/admin/issuances/{uuid.uuid4()}", headers={"Authorization": f"Bearer {admin}"}),
    )
    b = show(
        "GET real reference, client key",
        httpx.get(f"{BASE}/admin/issuances/{ref}", headers={"Authorization": f"Bearer {client}"}),
    )
    check("404 not_found and 403 forbidden", (a.status_code, b.status_code) == (404, 403))

    step("AC8 The database refuses the issuing role to read tokens and the HTTP role to update an issuance")
    ok = True
    for role, statement in (
        ("mcp_server", "SELECT * FROM tokens"),
        ("http_service", "UPDATE issuances SET bytes_consumed = 0"),
    ):
        password = "demo-" + secrets.token_hex(8)
        d.query(f"ALTER ROLE {role} PASSWORD '{password}'")  # as the migrator
        url = re.sub(r"//[^@]*@", f"//{role}:{password}@", os.environ["DATABASE_URL"])
        try:
            with psycopg.connect(url) as conn:
                conn.execute(statement)
            ok = False
            print(f"  psql as {role}> {statement}: ALLOWED")
        except psycopg.errors.InsufficientPrivilege as exc:
            print(f"  psql as {role}> {statement}: {str(exc).splitlines()[0]}")
    check("both denied", ok)

    step("AC9 make report, and the outcome counts behind it")
    d.query("SELECT outcome, count(*) FROM redemption_outcomes GROUP BY 1 ORDER BY 1")
    done = subprocess.run(
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"], capture_output=True, text=True, cwd=d.ROOT
    )  # noqa: S603
    lines = done.stdout.splitlines()
    print(
        "  "
        + "\n  ".join(
            ln
            for i, ln in enumerate(lines)
            if ln.startswith(("PANEL 1 ", "PANEL 3 ")) or lines[i - 1].startswith(("PANEL 1 ", "PANEL 3 "))
        )
    )
    # unknown 6 over (redeemed 1 + already_used 1 + expired 1 + unknown 6) = 6 of 9
    check(
        "success panel 6 of 9 (unknown over all except rate_limited)",
        any(": 6 of 9" in ln for ln in done.stdout.splitlines()),
    )
    check("redeemed panel 1 of 3 tokens issued today", any(": 1 of 3" in ln for ln in done.stdout.splitlines()))

    print("\n" + ("DEMO FAILED: " + "; ".join(d.FAILED) if d.FAILED else "DEMO PASSED"))
    return 1 if d.FAILED else 0


async def main() -> int:
    global BASE  # noqa: PLW0603
    os.environ.setdefault("KEY_HASH_SECRET", "demo-" + secrets.token_hex(16))
    pepper = os.environ.get("PEPPER") or os.environ.get("TOKEN_PEPPER") or "demo-" + secrets.token_hex(16)
    os.environ["PEPPER"] = os.environ["TOKEN_PEPPER"] = pepper
    mock_port, http_port = d.free_port(), d.free_port()
    os.environ["QRYPT_BASE_URL"] = f"http://127.0.0.1:{mock_port}"
    os.environ["QRYPT_API_KEY"] = d.MOCK_TOKEN
    BASE = f"http://127.0.0.1:{http_port}"
    mock = [sys.executable, "-m", "tests.mock_qrypt", "--port", str(mock_port), "--token", d.MOCK_TOKEN]
    http = [sys.executable, "-m", "uvicorn", "qrypt_entropy.transport.http_app:app", "--port", str(http_port)]
    with d.child(mock, ready="listening"), d.child(http):
        for _ in range(100):
            try:
                httpx.get(f"{BASE}/health")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        return await run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
