"""S23 gate cases: GET /admin/issuances/{reference} (docs/stories/S23-iss-trace.md AC1-AC6)."""

import json
import uuid
from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.service import access
from qrypt_entropy.transport.http_app import app

OWNER = "user-42"
CREATED = "2026-01-02T03:04:05+00:00"
REQUESTED = {0: "2026-01-01T00:00:00+00:00", 1: "2026-01-01T00:00:10+00:00"}
EXPIRES = "2026-01-02T03:14:05+00:00"
USED = "2026-01-02T03:05:00+00:00"


def same_instant(text, expected):
    return datetime.fromisoformat(text) == datetime.fromisoformat(expected)


@pytest.fixture
def trace(db):
    """A migrated database with an admin, a client and three issuances: two batches, a redeemed token, no token."""
    admin = access.create_credential("admin", "root")[1]
    client_id = access.create_credential("client", "billing-agent")[0].client_id
    refs = {name: str(uuid.uuid4()) for name in ("bytes", "token", "unused", "plain")}
    b1, b2 = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(db) as conn:
        for i, batch in enumerate((b1, b2)):
            conn.execute(
                "INSERT INTO batches (id, source, purpose, kib, requested_at) VALUES (%s, 'qrypt', 'refill', 1, %s)",
                (batch, REQUESTED[i]),
            )
        for name, tool, n in (
            ("bytes", "get_random_bytes", 4),
            ("token", "generate_token", 32),
            ("unused", "generate_token", 32),
            ("plain", "random_int", 8),
        ):
            conn.execute(
                "INSERT INTO issuances (id, client_id, tool, bytes_consumed, created_at) VALUES (%s, %s, %s, %s, %s)",
                (refs[name], client_id, tool, n, CREATED),
            )
        conn.execute(
            "INSERT INTO issuance_batches (issuance_id, batch_id, byte_offset, byte_count) VALUES "
            "(%s, %s, 1020, 3), (%s, %s, 0, 1), (%s, %s, 0, 32), (%s, %s, 0, 32), (%s, %s, 3, 8)",
            (refs["bytes"], b1, refs["bytes"], b2, refs["token"], b2, refs["unused"], b2, refs["plain"], b2),
        )
        for name, used in (("token", USED), ("unused", None)):
            conn.execute(
                "INSERT INTO tokens (id, issuance_id, owner, kind, token_hash, created_at, expires_at, used_at) "
                "VALUES (%s, %s, %s, 'otp_6', %s, %s, %s, %s)",
                (str(uuid.uuid4()), refs[name], OWNER, b"\x01" * 32, CREATED, EXPIRES, used),
            )
    http = TestClient(app, raise_server_exceptions=False)
    return http, {"Authorization": f"Bearer {admin}"}, refs, (b1, b2), client_id


def test_ac1_trace_shows_client_tool_size_and_batches(trace):
    http, admin, refs, (b1, b2), client_id = trace
    r = http.get(f"/admin/issuances/{refs['bytes']}", headers=admin)
    body = r.json()
    assert r.status_code == 200
    assert set(body) == {"reference", "client", "tool", "bytesConsumed", "createdAt", "batches", "token"}
    assert body["reference"] == refs["bytes"]
    assert body["client"] == {"id": client_id, "name": "billing-agent"}
    assert (body["tool"], body["bytesConsumed"]) == ("get_random_bytes", 4)
    assert same_instant(body["createdAt"], CREATED)
    assert len(body["batches"]) == 2
    assert {b["batchId"]: (b["source"], b["byteOffset"], b["byteCount"]) for b in body["batches"]} == {
        b1: ("qrypt", 1020, 3),
        b2: ("qrypt", 0, 1),
    }
    assert all(set(b) == {"batchId", "source", "byteOffset", "byteCount", "requestedAt"} for b in body["batches"])
    assert {
        b["batchId"]: same_instant(b["requestedAt"], REQUESTED[i])
        for i, b in enumerate(sorted(body["batches"], key=lambda b: b["requestedAt"]))
    } == {b1: True, b2: True}
    assert [b["batchId"] for b in body["batches"]] == [b1, b2]


def test_ac2_token_summary_has_no_hash_or_value(trace):
    http, admin, refs, *_ = trace
    r = http.get(f"/admin/issuances/{refs['token']}", headers=admin)
    body = r.json()
    assert set(body) == {"reference", "client", "tool", "bytesConsumed", "createdAt", "batches", "token"}
    token = body["token"]
    assert set(token) == {"owner", "kind", "expiresAt", "usedAt", "isSeed"}
    assert (token["owner"], token["kind"], token["isSeed"]) == (OWNER, "otp_6", False)
    assert same_instant(token["expiresAt"], EXPIRES) and same_instant(token["usedAt"], USED)
    assert "token_hash" not in r.text and "01" * 32 not in r.text and json.dumps("hash") not in r.text


def test_ac2_unredeemed_token_has_null_used_at(trace):
    http, admin, refs, *_ = trace
    token = http.get(f"/admin/issuances/{refs['unused']}", headers=admin).json()["token"]
    assert token["usedAt"] is None and same_instant(token["expiresAt"], EXPIRES)


def test_ac3_issuance_without_token_shows_null(trace):
    http, admin, refs, *_ = trace
    assert http.get(f"/admin/issuances/{refs['plain']}", headers=admin).json()["token"] is None


def test_ac4_unknown_is_404_and_malformed_is_400(trace):
    http, admin, *_ = trace
    r = http.get(f"/admin/issuances/{uuid.uuid4()}", headers=admin)
    assert (r.status_code, r.json()["error"]) == (404, "not_found")
    r = http.get("/admin/issuances/not-a-uuid", headers=admin)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_argument")


def test_ac5_client_key_is_403_and_no_key_is_401(trace):
    http, _, refs, *_ = trace
    _, client_key = access.create_credential("client", "other-agent")
    r = http.get(f"/admin/issuances/{refs['bytes']}", headers={"Authorization": f"Bearer {client_key}"})
    assert (r.status_code, r.json()["error"]) == (403, "forbidden")
    r = http.get(f"/admin/issuances/{refs['bytes']}")
    assert (r.status_code, r.json()["error"]) == (401, "unauthorized")


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "POST"])
def test_ac6_records_cannot_be_changed_through_the_product(trace, method):
    http, admin, refs, *_ = trace
    assert http.request(method, f"/admin/issuances/{refs['bytes']}", headers=admin).status_code == 405
    assert http.get(f"/admin/issuances/{refs['bytes']}", headers=admin).status_code == 200
