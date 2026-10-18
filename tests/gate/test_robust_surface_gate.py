"""S37 gate cases: every route and tool obeys its role, validation and speed limits (S37-robust-surface.md).

Real service: real Access, Pool, Meter, Issuance, Tokens and Postgres, the committed mock Qrypt, no stand-ins.
`make fuzz` (AC2), `make check-routes` (AC4) and `make route-latency` (AC5) run one case each; the authorization
matrix (AC1) lives in test_matrix_and_fuzz.py. All of them run inside `make gate`.
"""

import asyncio
import json
import os
import random
import statistics
import string
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from qrypt_entropy.config import get_settings
from qrypt_entropy.errors import HTTP_STATUS
from qrypt_entropy.service import access, meter, pool
from qrypt_entropy.service.access import create_client
from qrypt_entropy.transport import http_app, mcp_server
from qrypt_entropy.transport.http_app import app
from qrypt_entropy.transport.mcp_server import build_server
from tests.conftest import GATE_STATS, admin_key, base_url, client_key
from tests.gate.test_matrix_and_fuzz import REF, call_tool, http_routes  # noqa: F401  (REF reused below)
from tests.mock_qrypt import MockQrypt

KIB = 1024
ERROR_CODES = set(HTTP_STATUS) | set(http_app.REFUSAL_STATUS)  # /verify refusals are named outcomes
ROUTE_P95_LIMIT_S = 1.0  # PF-5


def bearer(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


def payload(result):
    return json.loads(result.content[0].text) if result.is_error else result.structured_content


class Crashes:
    """Replaces the transports' loggers: a crash log line is how an unhandled exception shows (it is caught and
    mapped to a 503 / tool error, so the status alone would hide it only if a 503 were also legitimate)."""

    def __init__(self):
        self.events = []

    def error(self, event, **fields):
        self.events.append(event)

    def __getattr__(self, name):  # info, warning, debug: ignored
        return lambda *args, **fields: None


@pytest.fixture
def crashes(monkeypatch):
    spy = Crashes()
    monkeypatch.setattr(http_app, "log", spy)
    monkeypatch.setattr(mcp_server, "log", spy)
    return spy


@pytest.fixture(autouse=True)
def restore_budget_cap():
    with psycopg.connect(base_url()) as conn:
        cap = conn.execute("SELECT cap_kib FROM budget").fetchone()[0]
    yield
    with psycopg.connect(base_url()) as conn:
        conn.execute("UPDATE budget SET cap_kib = %s", (cap,))


# ---------------------------------------------------------------------------------------------------------- AC2 fuzz


def junk(rng):
    text = string.printable + "é漢😀\u0000\ud800\x7f"
    kinds = [
        lambda: None,
        lambda: True,
        lambda: False,
        lambda: 0,
        lambda: -1,
        lambda: 2**53,
        lambda: 2**63,
        lambda: -(2**64),
        lambda: rng.randint(-(2**70), 2**70),
        lambda: rng.random() * 1e308,
        lambda: float("nan"),
        lambda: float("inf"),
        lambda: 1.5,
        lambda: "",
        lambda: " ",
        lambda: "\u0000",
        lambda: "\ud800",
        lambda: "x" * rng.randint(257, 5000),
        lambda: "".join(rng.choice(text) for _ in range(rng.randint(0, 40))),
        lambda: [],
        lambda: [1, "a"],
        lambda: {},
        lambda: {"a": [1, {}]},
        lambda: [[[[[]]]]],
        lambda: "hex",
        lambda: "otp_6",
        lambda: "-1",
    ]
    return rng.choice(kinds)()


TOOL_FIELDS = {
    "get_random_bytes": {"length": [1, 8, 1024, 1025, 0], "encoding": ["hex", "base64"]},
    "random_int": {"min": [-5, 0, 1, 2**53 - 1], "max": [1, 6, 2**53 - 1, 2**53]},
    "generate_token": {
        "kind": ["api_key", "otp_6", "uuid_v4"],
        "owner": ["u", "x" * 257],
        "expiry_seconds": [0, 5, 10**9],
    },
}


def tool_inputs(rng, count):
    """Mostly one field of an otherwise valid call replaced by junk, so the input gets past the first check."""
    for _ in range(count):
        name = rng.choice(list(TOOL_FIELDS))
        fields = TOOL_FIELDS[name]
        args = {f: rng.choice(opts) for f, opts in fields.items()}
        for f in rng.sample(list(fields), k=rng.randint(1, len(fields))):
            args[f] = junk(rng)
        for f in list(args):
            if rng.random() < 0.15:
                del args[f]
        if rng.random() < 0.1:
            args["extra"] = junk(rng)
        yield name, args


def route_inputs(rng, routes, count, keys):
    """(method, path, headers, raw body bytes): the right key often, so the input reaches validation."""
    names = {"owner": 0, "value": 0, "capKib": 0, "name": 0}
    for _ in range(count):
        route = rng.choice(routes)
        method = rng.choice([sorted(route.methods)[0]] * 5 + ["GET", "POST", "PUT", "DELETE", "PATCH"])
        path = route.path
        for param in ("reference", "client_id"):
            value = rng.choice([REF, quote(str(junk(rng))[:40], safe="", errors="replace"), "x", "%00", "..%2F", "é"])
            path = path.replace("{" + param + "}", value)
        if rng.random() < 0.1:
            path += rng.choice(["/", "/extra", "?a=" + "b" * 3000, "?x=%ff%00"])
        key = rng.choice(
            [keys["admin"]] * 3 + [keys["client"]] * 2 + [None, "".join(rng.choices(string.ascii_letters, k=8))]
        )
        headers = bearer(key)
        if rng.random() < 0.05:
            headers = {"Authorization": rng.choice(["Bearer", "Bearer ", "Basic abc", "Bearer " + "a" * 9000])}
        headers["Content-Type"] = rng.choice(
            ["application/json"] * 6 + ["text/plain", "application/x-www-form-urlencoded"]
        )
        shape = rng.random()
        if shape < 0.35:
            body = {k: junk(rng) for k in names if rng.random() < 0.8}
        elif shape < 0.55:
            body = junk(rng)
        elif shape < 0.7:
            body = {
                "owner": rng.choice(["u", "\u0000", "\ud800", "x" * 300]),
                "value": rng.choice(["1", "\ud800", "\u0000", "é"]),
            }
        else:
            body = {"capKib": rng.choice([0, 5, -1, 2**63, 2**63 - 1, 1.5, "7", None]), "name": junk(rng)}
        if rng.random() < 0.1:
            content = rng.choice([b"", b"{", b'{"owner":', b"\xff\xfe\x00", b"[" * 5000, os.urandom(50), b"null"])
        else:
            content = json.dumps(body).encode()  # allows NaN, Infinity, and escapes lone surrogates
        yield method, path, headers, content


async def test_fuzz_1000_malformed_inputs_cause_no_unhandled_error(crashes):
    """AC2 (RL-5): 500 tool calls and 500 HTTP requests of malformed input: no exception reaches the caller, no 5xx,
    every failure a structured 4xx / tool error with a stable code."""
    rng = random.Random(int(os.environ.get("FUZZ_SEED", "37")))
    routes = [r for r, _ in http_routes()]
    http = TestClient(app, raise_server_exceptions=False)
    keys = {"admin": admin_key(), "client": client_key()}
    refused = 0
    async with Client(build_server(lambda: client_key())) as client:
        for name, args in tool_inputs(rng, 500):
            try:
                result = await client.call_tool(name, args)
            except Exception as err:  # noqa: BLE001
                pytest.fail(f"unhandled exception for {name} {args!r:.200}: {type(err).__name__}: {err}")
            if result.is_error:
                refused += 1
                body = json.loads(result.content[0].text)
                # the upstream and the budget are healthy here, so a malformed call can only be the caller's fault
                assert body["error"] == "invalid_argument" and "value" not in body, (name, args, body)
    for method, path, headers, content in route_inputs(rng, routes, 500, keys):
        try:
            resp = http.request(method, path, headers=headers, content=content)
        except Exception as err:  # noqa: BLE001
            pytest.fail(f"unhandled exception for {method} {path[:80]}: {type(err).__name__}: {err}")
        assert resp.status_code < 500, (method, path[:80], resp.status_code, content[:80], resp.text[:120])
        if resp.status_code >= 400:
            assert resp.json()["error"] in ERROR_CODES, (method, path[:80], resp.text[:120])
    assert crashes.events == [], crashes.events
    assert refused > 100  # the generator really sends mostly invalid calls
    GATE_STATS["fuzz: malformed tool and route inputs, unhandled errors"] = "1000, 0"


# ----------------------------------------------- shared real-service fixture


@pytest.fixture
async def real(db, monkeypatch):
    """A migrated database, its own mock Qrypt, and fresh client and admin keys: the whole service, nothing stubbed."""
    async with MockQrypt() as mock:
        monkeypatch.setenv("QRYPT_BASE_URL", mock.url)
        monkeypatch.setenv("QRYPT_API_KEY", mock.token)
        get_settings.cache_clear()
        suffix = uuid.uuid4().hex[:8]
        keys = {role: access.create_credential(role, f"s37-{role}-{suffix}")[1] for role in ("client", "admin")}
        yield SimpleNamespace(db=db, mock=mock, keys=keys, http=TestClient(app, raise_server_exceptions=False))
    get_settings.cache_clear()


async def settle(level_floor: int = 60 * KIB) -> None:
    """Wait for the startup check and the first refill to land and stop moving, so before/after readings compare."""
    for _ in range(300):
        if pool.get_pool().level > level_floor:
            break
        await asyncio.sleep(0.02)
    previous = None
    for _ in range(100):
        now = (pool.get_pool().level, meter.get_usage()["budget"]["usedKib"])
        if now == previous:
            return
        previous = now
        await asyncio.sleep(0.3)
    pytest.fail("the pool did not settle")


def reading(real):
    """Pool level, KiB used, issuance rows and upstream requests: what a rejected call must leave alone."""
    with psycopg.connect(real.db) as conn:
        issuances = conn.execute("SELECT count(*) FROM issuances").fetchone()[0]
        batches = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
    return pool.get_pool().level, meter.get_usage()["budget"]["usedKib"], issuances, batches, len(real.mock.log)


# ---------------------------------------------------------------------------------------------------------- AC3

INVALID_TOOL_CASES = [
    ("get_random_bytes", {"length": 0, "encoding": "hex"}),
    ("get_random_bytes", {"length": -5, "encoding": "hex"}),
    ("get_random_bytes", {"length": 1025, "encoding": "hex"}),
    ("get_random_bytes", {"length": 2**64, "encoding": "hex"}),
    ("get_random_bytes", {"length": 8, "encoding": "binary"}),
    ("get_random_bytes", {"length": 1.5, "encoding": "hex"}),
    ("get_random_bytes", {"length": None, "encoding": "hex"}),
    ("get_random_bytes", {"encoding": "hex"}),
    ("random_int", {"min": 6, "max": 1}),
    ("random_int", {"min": 0, "max": 2**53}),
    ("random_int", {"min": -(2**53), "max": 0}),
    ("random_int", {"min": 1.5, "max": 6}),
    ("random_int", {"min": "a", "max": 6}),
    ("random_int", {"min": 1}),
    ("generate_token", {"kind": "pin"}),
    ("generate_token", {"kind": None}),
    ("generate_token", {"kind": "otp_6", "expiry_seconds": 60}),
    ("generate_token", {"kind": "otp_6", "owner": "u", "expiry_seconds": 0}),
    ("generate_token", {"kind": "otp_6", "owner": "u", "expiry_seconds": 10**9}),
    ("generate_token", {"kind": "otp_6", "owner": "x" * 257}),
    ("generate_token", {"kind": "otp_6", "owner": 5}),
    ("generate_token", {"kind": "otp_6", "owner": "a\u0000b"}),
    ("generate_token", {"kind": "otp_6", "owner": "\ud800"}),
    ("generate_token", {}),
]
INVALID_ROUTE_CASES = [
    ("POST", "/verify", {"owner": "u"}),
    ("POST", "/verify", {"owner": "", "value": "1"}),
    ("POST", "/verify", {"owner": "u", "value": 123456}),
    ("POST", "/verify", {"owner": "u", "value": "v" * 5000}),
    ("POST", "/verify", {"owner": "a\u0000b", "value": "1"}),
    ("POST", "/verify", {"owner": "\ud800", "value": "1"}),
    ("POST", "/verify", {"owner": "u", "value": "\ud800"}),
    ("POST", "/verify", [1, 2]),
    ("PUT", "/admin/budget", {"capKib": -1}),
    ("PUT", "/admin/budget", {"capKib": 2**63}),
    ("PUT", "/admin/budget", {"capKib": 1.5}),
    ("PUT", "/admin/budget", {"capKib": "10"}),
    ("PUT", "/admin/budget", {}),
    ("POST", "/admin/clients", {"name": ""}),
    ("POST", "/admin/clients", {}),
    ("GET", "/admin/issuances/not-a-uuid", None),
    ("GET", f"/admin/issuances/{uuid.uuid4()}", None),
    ("POST", "/admin/clients/not-a-uuid/revoke", None),
    ("POST", f"/admin/clients/{uuid.uuid4()}/revoke", None),
]


async def test_rejected_draws_nothing(real):
    """AC3 (CT-2): every invalid case of the tool and route criteria leaves the pool level, used_kib, the audit rows
    and the upstream request count exactly as they were."""
    cases = 0
    async with Client(build_server(lambda: real.keys["client"])) as client:
        await settle()
        before = reading(real)
        for name, args in INVALID_TOOL_CASES:
            result = await client.call_tool(name, args)
            assert result.is_error and payload(result)["error"] == "invalid_argument", (name, args, result)
            assert reading(real) == before, (name, args)
            cases += 1
        for who, key in (("none", None), ("admin", real.keys["admin"])):  # refused before the pool is touched
            result = await call_tool(key, "get_random_bytes", {"length": 8, "encoding": "hex"})
            assert result.is_error and payload(result)["error"] == ("unauthorized" if not key else "forbidden"), who
            assert reading(real) == before, who
            cases += 1
        for method, path, body in INVALID_ROUTE_CASES:
            headers = {**bearer(real.keys["admin"]), "Content-Type": "application/json"}
            resp = real.http.request(method, path, headers=headers, content=json.dumps(body))
            assert 400 <= resp.status_code < 500, (method, path, body, resp.status_code)
            assert reading(real) == before, (method, path, body)
            cases += 1
        for method, path in (("POST", "/verify"), ("GET", "/admin/usage"), ("PUT", "/admin/budget")):
            for key in (None, real.keys["client"] if path != "/verify" else "wrong-key"):
                resp = real.http.request(
                    method, path, headers=bearer(key), json={"owner": "u", "value": "1", "capKib": 5}
                )
                assert resp.status_code in (401, 403), (method, path, resp.status_code)
                assert reading(real) == before, (method, path)
                cases += 1
        valid = await client.call_tool("get_random_bytes", {"length": 8, "encoding": "hex"})
        assert not valid.is_error and reading(real)[2] == before[2] + 1  # the reading does see a real draw
    GATE_STATS["rejected-draws-nothing: invalid cases, pool and meter unchanged"] = str(cases)


# ---------------------------------------------------------------------------------------------------------- AC6


async def failing_call(real, key, prepare):
    """One get_random_bytes against a fresh server whose upstream or budget was set up by `prepare`."""
    await pool.shutdown()
    pool.use_pool(None)
    prepare()
    async with Client(build_server(lambda: key)) as client:
        result = await client.call_tool("get_random_bytes", {"length": 8, "encoding": "hex"})
    assert result.is_error
    return payload(result)


async def test_error_codes_live(real):
    """AC6 (CT-4): one request per code, across the service, each with its stable code (and HTTP status on a route)."""
    seen = {}
    key, admin = real.keys["client"], real.keys["admin"]
    async with Client(build_server(lambda: key)) as client:  # a healthy server for the caller-fault codes
        seen["invalid_argument"] = payload(await client.call_tool("random_int", {"min": 2, "max": 1}))
    unauthorized = real.http.get("/admin/usage")
    forbidden = real.http.get("/admin/usage", headers=bearer(key))
    missing = real.http.get(f"/admin/issuances/{uuid.uuid4()}", headers=bearer(admin))
    assert (unauthorized.status_code, forbidden.status_code, missing.status_code) == (401, 403, 404)
    seen["unauthorized"], seen["forbidden"], seen["not_found"] = unauthorized.json(), forbidden.json(), missing.json()
    bad_body = real.http.post("/verify", headers=bearer(key), json={"owner": ""})
    assert bad_body.status_code == 400 and bad_body.json()["error"] == "invalid_argument"

    cap = meter.get_usage()["budget"]["capKib"]
    meter.set_budget(0)  # nothing may be reserved: the startup check and every refill are refused
    seen["budget_exhausted"] = await failing_call(real, key, lambda: None)
    meter.set_budget(cap)
    real.mock.sticky = 401  # Qrypt rejects our token
    seen["upstream_misconfigured"] = await failing_call(real, key, lambda: None)
    real.mock.sticky = 503  # Qrypt is down
    seen["temporarily_unavailable"] = await failing_call(real, key, lambda: None)
    real.mock.sticky = None

    for code, body in seen.items():
        assert body["error"] == code and "value" not in body, (code, body)
    assert {
        "invalid_argument",
        "unauthorized",
        "budget_exhausted",
        "temporarily_unavailable",
        "upstream_misconfigured",
    } <= set(seen)
    GATE_STATS["error-codes-live: codes seen with their stable code"] = ", ".join(sorted(seen))


# ---------------------------------------------------------------------------------------------------------- AC4

HOSTED = {  # method, path: (role that may call it, the response keys it may carry)
    ("GET", "/health"): (
        None,
        {
            "status",
            "database",
            "pool",
            "upstream",
            "reporting",
            "reportAgeSeconds",
            "ok",
            "error",
            "message",
            "components",
            "detail",
        },
    ),
    ("GET", "/metrics"): ("admin", None),  # Prometheus text, checked below
    ("POST", "/verify"): ("client", {"outcome", "error", "message"}),
    ("GET", "/admin/usage"): ("admin", {"budget", "perClient"}),
    ("PUT", "/admin/budget"): ("admin", {"budget"}),
    ("GET", "/admin/issuances/{reference}"): (
        "admin",
        {"reference", "client", "tool", "bytesConsumed", "createdAt", "batches", "token"},
    ),
    ("POST", "/admin/clients"): ("admin", {"client", "key", "name", "id", "role", "createdAt", "revokedAt"}),
    ("POST", "/admin/clients/{client_id}/revoke"): (
        "admin",
        {"client", "id", "name", "role", "revokedAt", "createdAt", "revoked"},
    ),
}
ENTROPY_KEYS = {"value", "token", "bytes", "random", "randomInt", "int", "otp", "secret"}


async def test_no_hosted_route_returns_an_entropy_derived_value(real):
    """AC4 (GC-7): the deployed route list is exactly the hosted set; each route called once with the right key
    returns no random bytes, integer or token: not under an entropy-like key, and not any value this server issued."""
    deployed = {(sorted(r.methods)[0], r.path) for r, _ in http_routes()}
    assert deployed == set(HOSTED), deployed ^ set(HOSTED)
    async with Client(build_server(lambda: real.keys["client"])) as client:  # issue one of each value first
        issued = [payload(await client.call_tool("get_random_bytes", {"length": 16, "encoding": "hex"}))]
        issued.append(payload(await client.call_tool("get_random_bytes", {"length": 16, "encoding": "base64"})))
        issued.append(payload(await client.call_tool("random_int", {"min": 100000, "max": 999999})))
        issued.append(payload(await client.call_tool("generate_token", {"kind": "api_key", "owner": "o"})))
        issued.append(payload(await client.call_tool("generate_token", {"kind": "uuid_v4"})))
    values = {str(i["value"]) for i in issued}
    ref = issued[0]["metadata"]["reference"]
    victim = real.http.post("/admin/clients", headers=bearer(real.keys["admin"]), json={"name": "victim"}).json()
    ids = {"{reference}": ref, "{client_id}": victim["client"]["id"]}
    rows = []
    for (method, path), (role, allowed_keys) in sorted(HOSTED.items()):
        key = {"client": real.keys["client"], "admin": real.keys["admin"], None: None}[role]
        url = path
        for name, value in ids.items():
            url = url.replace(name, value)
        body = {
            "/verify": {"owner": "o", "value": "000000"},
            "/admin/budget": {"capKib": 1000000},
            "/admin/clients": {"name": "once"},
        }.get(path)
        resp = real.http.request(method, url, headers=bearer(key), json=body)
        assert resp.status_code in (200, 201, 404), (method, path, resp.status_code, resp.text[:100])
        assert not any(v in resp.text for v in values), (method, path)
        if allowed_keys is not None:
            data = resp.json()
            assert isinstance(data, dict) and set(data) <= allowed_keys, (path, set(data) - allowed_keys)
            assert not _entropy_leaves(data), (path, _entropy_leaves(data))
        else:  # /metrics: Prometheus text; only metric lines and comments
            assert all(
                line.startswith("#") or line.split("{")[0].split(" ")[0].startswith("qrypt_")
                for line in resp.text.splitlines()
                if line
            ), resp.text[:200]
        rows.append(f"{method} {path} -> {resp.status_code}")
    GATE_STATS["check-routes: hosted routes called once, entropy-derived values returned"] = f"{len(rows)} routes, 0"
    print(
        "\n".join(
            ["hosted routes (right key, one call each):", *rows, "none returned random bytes, an integer or a token"]
        )
    )


def _entropy_leaves(data) -> set:
    """Entropy-like keys that carry a plain value (a string or number); a summary object under `token` is fine."""
    if isinstance(data, dict):
        found = {k for k, v in data.items() if k in ENTROPY_KEYS and isinstance(v, str | int | float)}
        return found | {k for v in data.values() for k in _entropy_leaves(v)}
    if isinstance(data, list):
        return {k for v in data for k in _entropy_leaves(v)}
    return set()


# ---------------------------------------------------------------------------------------------------------- AC5


def test_route_latency_p95(real):
    """AC5 (PF-5): 100 requests to each of POST /verify, the admin routes and GET /metrics: p95 within 1 s, the
    figures written to the results file."""
    http = TestClient(app)
    admin, client = bearer(real.keys["admin"]), bearer(real.keys["client"])
    ref = str(uuid.uuid4())
    with psycopg.connect(real.db) as conn:
        batch = "00000000-0000-4000-8000-0000000000b1"
        owner = conn.execute("SELECT id FROM credentials WHERE role = 'client' LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO batches (id, purpose, kib, outcome) VALUES (%s,'refill',64,'received')", (batch,))
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s,%s,'get_random_bytes',8)",
            (ref, owner),
        )
        conn.execute(
            "INSERT INTO issuance_batches (issuance_id, batch_id, byte_offset, byte_count) VALUES (%s,%s,0,8)",
            (ref, batch),
        )
    victims = [create_client(f"latency-{i}-{uuid.uuid4().hex[:6]}")["client"]["id"] for i in range(100)]
    cap = meter.get_usage()["budget"]["capKib"]
    plan = {
        "POST /verify": lambda i: http.post("/verify", headers=client, json={"owner": f"lat-{i}", "value": f"{i:06d}"}),
        "GET /metrics": lambda i: http.get("/metrics", headers=admin),
        "GET /admin/usage": lambda i: http.get("/admin/usage", headers=admin),
        "PUT /admin/budget": lambda i: http.put("/admin/budget", headers=admin, json={"capKib": cap}),
        "GET /admin/issuances/{reference}": lambda i: http.get(f"/admin/issuances/{ref}", headers=admin),
        "POST /admin/clients": lambda i: http.post(
            "/admin/clients", headers=admin, json={"name": f"n{i}-{uuid.uuid4().hex[:6]}"}
        ),
        "POST /admin/clients/{client_id}/revoke": lambda i: http.post(
            f"/admin/clients/{victims[i]}/revoke", headers=admin
        ),
    }
    figures = {}
    for route, send in plan.items():
        send(0)  # an awake service: the first request opens the database connection
        durations = []
        for i in range(100):
            started = time.perf_counter()
            resp = send(i)
            durations.append(time.perf_counter() - started)
            assert resp.status_code in (200, 201, 404), (route, resp.status_code)
        q = statistics.quantiles(durations, n=100)
        figures[route] = {
            "requests": 100,
            "p50_ms": round(q[49] * 1000, 2),
            "p95_ms": round(q[94] * 1000, 2),
            "p99_ms": round(q[98] * 1000, 2),
        }
        assert q[94] <= ROUTE_P95_LIMIT_S, (route, figures[route])
    out = Path(os.environ.get("ROUTE_LATENCY_FILE", "results/route_latency.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"limit_p95_ms": ROUTE_P95_LIMIT_S * 1000, "database": real.db.rsplit("@", 1)[-1], "routes": figures},
            indent=2,
        )
    )
    worst = max(f["p95_ms"] for f in figures.values())
    GATE_STATS["route p95 (ms), worst of 7 routes x 100 requests, limit"] = f"{worst} <= {ROUTE_P95_LIMIT_S * 1000:.0f}"
    print("route p95 (ms): " + ", ".join(f"{r} {f['p95_ms']}" for r, f in figures.items()) + f" -> {out}")
