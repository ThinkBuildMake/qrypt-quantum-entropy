"""S24 gate cases: usage view, budget setting, `budget-lowered-below-used`, `usage-derivable-from-audit`."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from qrypt_entropy.errors import InvalidArgument
from qrypt_entropy.service import access, meter
from qrypt_entropy.transport.http_app import app


@pytest.fixture
def http(db):
    yield TestClient(app, raise_server_exceptions=False)
    meter.close_pools()


@pytest.fixture
def keys(db):
    return {
        "admin": {"Authorization": f"Bearer {access.create_credential('admin', 'sam')[1]}"},
        "client": {"Authorization": f"Bearer {access.create_credential('client', 'a-client')[1]}"},
    }


def make_client(db, name):
    return str(access.create_credential("client", name)[0].client_id)


def issue(db, client_id, nbytes, when=None):
    with psycopg.connect(db) as conn:
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed, created_at) "
            "VALUES (%s, %s, 'get_random_bytes', %s, COALESCE(%s, now()))",
            (uuid.uuid4(), client_id, nbytes, when),
        )


def set_used(db, used, cap=10240):
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE budget SET cap_kib = %s, used_kib = %s", (cap, used))


def test_ac1_usage_shows_cap_used_and_bytes_per_client(http, keys, db):
    set_used(db, 128)
    billing, support = make_client(db, "billing-agent"), make_client(db, "support-agent")
    issue(db, billing, 600)
    issue(db, billing, 300)
    issue(db, support, 100)
    r = http.get("/admin/usage", headers=keys["admin"])
    assert r.status_code == 200
    body = r.json()
    assert body["budget"] == {"capKib": 10240, "usedKib": 128}
    assert {(c["name"], c["bytesConsumed"]) for c in body["perClient"]} == {
        ("billing-agent", 900),
        ("support-agent", 100),
    }
    assert {c["clientId"] for c in body["perClient"]} == {billing, support}


def test_ac2_empty_system_is_an_empty_valid_view(http, keys):
    r = http.get("/admin/usage", headers=keys["admin"])
    assert (r.status_code, r.json()) == (200, {"budget": {"capKib": 10240, "usedKib": 0}, "perClient": []})


def test_ac3_budget_can_be_set_and_used_is_unchanged(http, keys, db):
    set_used(db, 128)
    r = http.put("/admin/budget", headers=keys["admin"], json={"capKib": 500})
    assert (r.status_code, r.json()) == (200, {"budget": {"capKib": 500, "usedKib": 128}})
    assert http.get("/admin/usage", headers=keys["admin"]).json()["budget"] == {"capKib": 500, "usedKib": 128}


def test_gate_budget_lowered_below_used(http, keys, db):
    set_used(db, 128)
    assert http.put("/admin/budget", headers=keys["admin"], json={"capKib": 100}).status_code == 200
    assert http.get("/admin/usage", headers=keys["admin"]).json()["budget"] == {"capKib": 100, "usedKib": 128}
    with pytest.raises(meter.BudgetExhausted):
        meter.reserve(1, "refill", "qrypt")


@pytest.mark.parametrize("bad", [-1, 1.5, "many", True, None, 2**63, 1e3])
def test_ac5_bad_budget_values_are_refused(http, keys, db, bad):
    set_used(db, 128)
    r = http.put("/admin/budget", headers=keys["admin"], json={"capKib": bad})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_argument")
    assert http.get("/admin/usage", headers=keys["admin"]).json()["budget"]["capKib"] == 10240


def test_ac5_missing_or_malformed_body_is_refused(http, keys):
    for content in (b"", b"[]", b'{"cap":5}', b"{not json"):
        r = http.put("/admin/budget", headers={**keys["admin"], "Content-Type": "application/json"}, content=content)
        assert (r.status_code, r.json()["error"]) == (400, "invalid_argument")


def test_set_budget_function_rejects_bad_values(db):
    for bad in (-1, 1.5, "many", True, 2**63):
        with pytest.raises(InvalidArgument):
            meter.set_budget(bad)  # type: ignore[arg-type]
    meter.close_pools()


@pytest.mark.parametrize(("method", "body"), [("GET", None), ("PUT", {"capKib": 5})], ids=["usage", "budget"])
def test_ac6_client_key_is_forbidden_and_no_key_unauthorized(http, keys, db, method, body):
    path = "/admin/usage" if method == "GET" else "/admin/budget"
    set_used(db, 0)
    r = http.request(method, path, headers=keys["client"], json=body)
    assert (r.status_code, r.json()["error"]) == (403, "forbidden")
    r = http.request(method, path, json=body)
    assert (r.status_code, r.json()["error"]) == (401, "unauthorized")
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT cap_kib FROM budget").fetchone()[0] == 10240


def test_gate_usage_derivable_from_audit(http, keys, db):
    a, b = make_client(db, "agent-a"), make_client(db, "agent-b")
    for n, day in enumerate(("2026-10-16 09:00+00", "2026-10-17 01:00+00", "2026-10-17 23:59+00")):
        issue(db, a, 10 + n, day)
        issue(db, b, 100 + n, day)
    usage = http.get("/admin/usage", headers=keys["admin"]).json()["perClient"]
    with psycopg.connect(db) as conn:
        audit = dict(
            conn.execute(
                "SELECT client_id::text, sum(bytes_consumed)::bigint FROM issuances GROUP BY client_id"
            ).fetchall()
        )
        daily = conn.execute(
            "SELECT client_id::text, sum(bytes_consumed)::bigint FROM issuances "
            "WHERE created_at::date <= '2026-10-17' GROUP BY client_id"
        ).fetchall()
    shown = {c["clientId"]: c["bytesConsumed"] for c in usage}
    assert {k: shown[k] - audit[k] for k in audit} == {a: 0, b: 0}
    assert set(shown) == set(audit) and dict(daily) == audit
    assert sum(shown.values()) == 33 + 303


def test_set_budget_rearms_the_100_percent_alert(db, caplog):
    import logging

    set_used(db, 10, cap=10)
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            with pytest.raises(meter.BudgetExhausted):
                meter.reserve(1, "refill", "qrypt")
        meter.set_budget(10)  # raising or re-setting the cap starts a new episode
        with pytest.raises(meter.BudgetExhausted):
            meter.reserve(1, "refill", "qrypt")
    assert sum("budget_alert" in r.getMessage() for r in caplog.records) == 2
    meter.close_pools()


def test_set_budget_works_with_only_the_http_service_grants(db):
    """meter.set_budget itself, connected as http_service: it may update only cap_kib and updated_at."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("ALTER ROLE http_service PASSWORD 'http-test-only'")
    http_url = make_url(db).set(username="http_service", password="http-test-only").render_as_string(False)
    assert meter.set_budget(7, url=http_url) == {"budget": {"capKib": 7, "usedKib": 0}}
    assert meter.get_usage(url=http_url)["budget"]["capKib"] == 7
    meter.close_pools()
