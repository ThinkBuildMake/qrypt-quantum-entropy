import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.transport.http_app import app
from tests.conftest import admin_key, seed_issuance

REF = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def http():
    return TestClient(app)


def test_ac3_health_has_contract_fields(http):
    body = http.get("/health").json()  # shapes with data are covered by tests/test_health.py (S29)
    assert set(body) == {"reporting", "reportAgeSeconds", "pool", "upstream"}


def test_ac5_usage_and_issuance_shapes(http):
    seed_issuance(REF)
    usage = http.get("/admin/usage", headers={"Authorization": f"Bearer {admin_key()}"}).json()
    assert set(usage) == {"budget", "perClient"}
    assert set(usage["budget"]) == {"capKib", "usedKib"}
    issuance = http.get(f"/admin/issuances/{REF}", headers={"Authorization": f"Bearer {admin_key()}"}).json()
    assert issuance["reference"] == REF
    assert {"client", "tool", "bytesConsumed", "createdAt", "batches", "token"} <= set(issuance)


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/verify"), ("GET", "/admin/usage"), ("GET", f"/admin/issuances/{REF}")],
)
def test_ac5_no_key_is_401_unauthorized(http, method, path):
    r = http.request(method, path, json={"owner": "u", "value": "1"} if method == "POST" else None)
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"
