"""H02 gate cases: POST /admin/trace-token, an owner and a token value in, the issuance provenance out (F5b)."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.service import access, tokens
from qrypt_entropy.transport.http_app import app

OWNER, VALUE = "user-42", "731905"


@pytest.fixture
def traced(db):
    admin = access.create_credential("admin", "root")[1]
    client_id = access.create_credential("client", "billing-agent")[0].client_id
    reference = str(uuid.uuid4())
    with psycopg.connect(db) as conn:
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'generate_token', 3)",
            (reference, client_id),
        )
        conn.execute(
            "INSERT INTO tokens (id, issuance_id, owner, kind, token_hash, expires_at) "
            "VALUES (%s, %s, %s, 'otp_6', %s, now() + interval '10 minutes')",
            (str(uuid.uuid4()), reference, OWNER, tokens.hash_token(OWNER, VALUE)),
        )
    tokens.store_seed_token("seed-owner", "otp_6", "123456", 600)
    return TestClient(app, raise_server_exceptions=False), {"Authorization": f"Bearer {admin}"}, reference


def test_owner_and_value_resolve_to_the_issuance(traced):
    http, admin, reference = traced
    r = http.post("/admin/trace-token", headers=admin, json={"owner": OWNER, "value": VALUE})
    assert r.status_code == 200 and r.json()["reference"] == reference
    assert r.json()["token"]["owner"] == OWNER
    assert VALUE not in r.text and "tokenHash" not in r.text


def test_lookup_is_not_a_redemption(traced):
    http, admin, _ = traced
    http.post("/admin/trace-token", headers=admin, json={"owner": OWNER, "value": VALUE})
    assert http.post("/admin/trace-token", headers=admin, json={"owner": OWNER, "value": VALUE}).status_code == 200
    assert tokens.redeem(OWNER, VALUE) == "redeemed"


@pytest.mark.parametrize(
    "body",
    [
        {"owner": OWNER, "value": "000000"},  # wrong value
        {"owner": "someone-else", "value": VALUE},  # right value, wrong owner
        {"owner": "seed-owner", "value": "123456"},  # a seed token has no issuance
    ],
)
def test_unknown_token_is_one_uniform_404_that_leaks_nothing(traced, body):
    http, admin, reference = traced
    r = http.post("/admin/trace-token", headers=admin, json=body)
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "no such token"}
    assert body["value"] not in r.text and reference not in r.text


def test_client_key_is_403_and_no_key_is_401(traced):
    http, _, _ = traced
    client = access.create_credential("client", "other-agent")[1]
    body = {"owner": OWNER, "value": VALUE}
    r = http.post("/admin/trace-token", headers={"Authorization": f"Bearer {client}"}, json=body)
    assert (r.status_code, r.json()["error"]) == (403, "forbidden") and VALUE not in r.text
    r = http.post("/admin/trace-token", json=body)
    assert (r.status_code, r.json()["error"]) == (401, "unauthorized")


def test_invalid_body_is_400_and_does_not_echo_the_value(traced):
    http, admin, _ = traced
    r = http.post("/admin/trace-token", headers=admin, json={"owner": "", "value": "secret-value-9"})
    assert r.status_code == 400 and "secret-value-9" not in r.text


def test_failed_lookups_share_the_redemption_failure_limit(traced):
    http, admin, _ = traced
    from qrypt_entropy.config import get_settings

    for i in range(get_settings().rate_limit_max_failures):
        r = http.post("/admin/trace-token", headers=admin, json={"owner": OWNER, "value": f"00000{i}"})
        assert r.status_code == 404
    r = http.post("/admin/trace-token", headers=admin, json={"owner": OWNER, "value": VALUE})  # even the right code
    assert r.status_code == 503 and VALUE not in r.text and "reference" not in r.json()
