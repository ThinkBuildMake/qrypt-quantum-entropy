"""AC8/AC10: authorization matrix over every registered tool and route, a 1,000-input fuzz pass, and the
stub tool-call p95 check. These run inside `make gate`."""

import json
import os
import random
import statistics
import string
import time
import uuid
from urllib.parse import quote

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from mcp.client import Client

from qrypt_entropy.service.access import STUB_ADMIN_KEY, STUB_CLIENT_KEY, create_client
from qrypt_entropy.transport.http_app import app
from qrypt_entropy.transport.mcp_server import TOOL_ROLES, build_server
from tests.conftest import GATE_STATS

CREDENTIALS = {"none": None, "client": STUB_CLIENT_KEY, "admin": STUB_ADMIN_KEY}
ROLE_OF = {"none": None, "client": "client", "admin": "admin"}
VALID_TOOL_ARGS = {
    "get_random_bytes": {"length": 8, "encoding": "hex"},
    "random_int": {"min": 1, "max": 6},
    "generate_token": {"kind": "otp_6"},
}
REF = "11111111-1111-4111-8111-111111111111"
KNOWN_CODES = {"invalid_argument", "unauthorized", "forbidden", "not_found", "budget_exhausted",
               "temporarily_unavailable", "upstream_misconfigured", "database_unavailable"}  # fmt: skip


def http_routes():
    for route in app.routes:
        if isinstance(route, APIRoute):
            roles = None
            for dep in route.dependant.dependencies:
                if hasattr(dep.call, "allowed_roles"):
                    roles = dep.call.allowed_roles
            yield route, roles  # roles None means the route is open


def sample_path(path, client_id=REF):
    return path.replace("{reference}", REF).replace("{client_id}", client_id)


def sample_body(route):
    paths = {"/verify": {"owner": "user-42", "value": "123456"}, "/admin/budget": {"capKib": 10},
             "/admin/clients": {"name": f"matrix-{uuid.uuid4().hex}"}}  # fmt: skip
    return paths.get(route.path)


async def call_tool(key, name, args):
    async with Client(build_server(lambda: key)) as client:
        return await client.call_tool(name, args)


def error_code(text):
    return json.loads(text)["error"]


async def test_ac10_authorization_matrix_over_every_tool_and_route():
    cells = 0
    server = build_server(lambda: None)
    async with Client(server) as client:
        registered = {t.name for t in (await client.list_tools()).tools}
    assert registered == set(TOOL_ROLES), "every registered tool needs an entry in TOOL_ROLES"
    for name in registered:
        for who, key in CREDENTIALS.items():
            result = await call_tool(key, name, VALID_TOOL_ARGS[name])
            allowed = ROLE_OF[who] in TOOL_ROLES[name]
            assert (not result.is_error) == allowed, (name, who)
            if not allowed:
                assert error_code(result.content[0].text) == ("unauthorized" if who == "none" else "forbidden")
            cells += 1
    http = TestClient(app, raise_server_exceptions=False)
    routes = list(http_routes())
    assert len(routes) >= 8
    existing_client = create_client(f"matrix-{uuid.uuid4().hex}")["client"]["id"]
    for route, roles in routes:
        method = sorted(route.methods)[0]
        for who, key in CREDENTIALS.items():
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            resp = http.request(
                method, sample_path(route.path, existing_client), headers=headers, json=sample_body(route)
            )
            allowed = roles is None or ROLE_OF[who] in roles
            # /verify answers a refusal outcome (404 for the sample body) once the caller is authorized
            passed = resp.status_code in (200, 404) if route.path == "/verify" else resp.status_code < 400
            assert passed == allowed, (method, route.path, who, resp.status_code)
            if not allowed:
                assert resp.status_code == (401 if who == "none" else 403)
            cells += 1
    GATE_STATS["authorization matrix cells run"] = str(cells)
    assert cells == 3 * (len(registered) + len(routes))


def junk(rng):
    kinds = [
        lambda: None, lambda: rng.randint(-(2**70), 2**70), lambda: rng.random() * 1e308, lambda: True,
        lambda: "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 40))),
        lambda: "x" * rng.randint(257, 5000), lambda: [], lambda: {"a": [1, {}]}, lambda: "\u0000퟿",
        lambda: float("inf"),
    ]  # fmt: skip
    return rng.choice(kinds)()


async def test_ac10_fuzz_1000_inputs_cause_no_unhandled_exception_or_5xx():
    rng = random.Random(1234)
    server = build_server(lambda: STUB_CLIENT_KEY)
    http = TestClient(app, raise_server_exceptions=False)
    routes = [(r, sorted(r.methods)[0]) for r, _ in http_routes()]
    fields = {"get_random_bytes": ["length", "encoding"], "random_int": ["min", "max"],
              "generate_token": ["kind", "owner", "expiry_seconds"]}  # fmt: skip
    total = 0
    async with Client(server) as client:
        for _ in range(500):
            name = rng.choice(list(fields))
            args = {f: junk(rng) for f in fields[name] if rng.random() < 0.8}
            if rng.random() < 0.1:
                args["extra"] = junk(rng)
            try:
                result = await client.call_tool(name, args)
            except Exception as err:  # noqa: BLE001
                pytest.fail(f"unhandled exception for {name}: {type(err).__name__}")
            if result.is_error:
                assert error_code(result.content[0].text) in KNOWN_CODES, (name, result.content[0].text[:80])
            total += 1
    for _ in range(500):
        route, method = rng.choice(routes)
        key = rng.choice([STUB_ADMIN_KEY, STUB_CLIENT_KEY, None, "".join(rng.choices(string.ascii_letters, k=8))])
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        path = route.path.replace("{reference}", quote(str(junk(rng))[:30], safe="") or "x")
        path = path.replace("{client_id}", quote(str(junk(rng))[:30], safe="") or "x")
        body = junk(rng) if rng.random() < 0.5 else {k: junk(rng) for k in ("owner", "value", "capKib", "name")}
        try:
            resp = http.request(
                method,
                path,
                headers={**headers, "Content-Type": "application/json"},
                content=json.dumps(body),  # allows NaN/Infinity, which json= refuses
            )
        except Exception as err:  # noqa: BLE001
            pytest.fail(f"unhandled exception for {method} {route.path}: {type(err).__name__}")
        assert resp.status_code < 500, (method, route.path, resp.status_code)
        total += 1
    GATE_STATS["fuzz inputs run"] = str(total)
    assert total == 1000


async def test_ac8_stub_tool_call_p95_is_under_the_threshold():
    threshold = float(os.environ.get("GATE_P95_MS", "50"))
    durations = []
    async with Client(build_server(lambda: STUB_CLIENT_KEY)) as client:
        for i in range(1000):
            name = list(VALID_TOOL_ARGS)[i % 3]
            started = time.perf_counter()
            await client.call_tool(name, VALID_TOOL_ARGS[name])
            durations.append((time.perf_counter() - started) * 1000)
    p95 = statistics.quantiles(durations, n=100)[94]
    GATE_STATS["stub tool-call p95 (ms) over 1000 calls, threshold"] = f"{p95:.2f} < {threshold}"
    assert p95 < threshold
