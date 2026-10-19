"""S05: the admin routes and the credentials write path, against a real migrated database."""

import logging
import shutil
import subprocess

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access
from qrypt_entropy.transport.http_app import app


@pytest.fixture
def admin(db):
    _, key = access.create_credential("admin", "root")
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def http():
    return TestClient(app, raise_server_exceptions=False)


def create(http, admin, name="billing-agent"):
    return http.post("/admin/clients", headers=admin, json={"name": name})


def test_ac1_admin_creates_a_client_and_receives_its_key_once(http, admin):
    r = create(http, admin)
    body = r.json()
    assert r.status_code == 201
    assert body["client"]["name"] == "billing-agent"
    assert (body["client"]["role"], body["client"]["status"]) == ("client", "active")
    assert len(body["key"]) >= 32
    assert access.authenticate(body["key"], {"client"}).client_id == body["client"]["id"]


@pytest.mark.parametrize("body", [{"name": "billing-agent"}, {"name": ""}, {"name": "   "}, {"name": "a\u0000b"}, {}])
def test_ac2_duplicate_or_empty_name_is_invalid_argument(http, admin, body):
    assert create(http, admin).status_code == 201
    r = http.post("/admin/clients", headers=admin, json=body)
    assert (r.status_code, r.json()["error"]) == (400, "invalid_argument")


def test_ac3_key_is_nowhere_in_storage_or_logs(http, admin, db, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    key = create(http, admin).json()["key"]
    access.revoke_client(_client_id(db))
    with pytest.raises(access.Unauthorized):
        access.authenticate(key, {"client"})
    with psycopg.connect(db) as conn:
        stored = "\n".join(str(row) for row in conn.execute("SELECT * FROM credentials").fetchall())
    assert key not in stored
    if shutil.which("pg_dump"):
        dump = subprocess.run(["pg_dump", db], capture_output=True, text=True, check=True).stdout
        assert key not in dump
    captured = capfd.readouterr()
    assert key not in caplog.text + captured.out + captured.err


def _client_id(db):
    with psycopg.connect(db) as conn:
        return str(conn.execute("SELECT id FROM credentials WHERE role = 'client'").fetchone()[0])


def test_ac4_admin_revokes_a_client(http, admin, db):
    created = create(http, admin).json()
    r = http.post(f"/admin/clients/{created['client']['id']}/revoke", headers=admin)
    assert r.status_code == 200
    assert r.json() == {"client": {**created["client"], "status": "revoked"}}
    with psycopg.connect(db) as conn:
        assert conn.execute(
            "SELECT revoked_at IS NOT NULL FROM credentials WHERE name = 'billing-agent'"
        ).fetchone() == (True,)


@pytest.mark.parametrize("client_id", ["3f1c2a9e-5b7d-4e86-9a40-1d2e3f4a5b6c", "not-a-uuid"])
def test_ac5_revoking_an_unknown_client_is_not_found(http, admin, client_id):
    r = http.post(f"/admin/clients/{client_id}/revoke", headers=admin)
    assert (r.status_code, r.json()["error"]) == (404, "not_found")


def test_revoking_an_admin_credential_is_not_found(http, admin, db):
    admin_id = access.create_credential("admin", "second")[0].client_id
    assert http.post(f"/admin/clients/{admin_id}/revoke", headers=admin).status_code == 404


def test_revoking_twice_keeps_the_first_revocation_time(http, admin, db):
    cid = create(http, admin).json()["client"]["id"]
    http.post(f"/admin/clients/{cid}/revoke", headers=admin)
    with psycopg.connect(db) as conn:
        first = conn.execute("SELECT revoked_at FROM credentials WHERE id = %s", (cid,)).fetchone()
    assert http.post(f"/admin/clients/{cid}/revoke", headers=admin).status_code == 200
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT revoked_at FROM credentials WHERE id = %s", (cid,)).fetchone() == first


def test_create_credential_rejects_an_unknown_role(db):
    with pytest.raises(access.InvalidArgument):
        access.create_credential("root", "x")


def test_database_down_is_temporarily_unavailable(monkeypatch):
    access.close_pools()
    monkeypatch.setattr("qrypt_entropy.service.access.open_pool", lambda url, **kw: _raise_operational())
    with pytest.raises(access.TemporarilyUnavailable):
        access.authenticate("k", {"client"})
    access.close_pools()


def _raise_operational():
    raise psycopg.OperationalError("down")


def test_missing_hash_secret_refuses_rather_than_hashing_with_nothing(monkeypatch):
    monkeypatch.delenv("KEY_HASH_SECRET")
    get_settings.cache_clear()
    with pytest.raises(access.TemporarilyUnavailable):
        access.hash_key("k")
    get_settings.cache_clear()
