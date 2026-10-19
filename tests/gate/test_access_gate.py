"""S05 gate cases: authenticate-unauthorized, authenticate-forbidden, keys-from-os."""

import logging

import pytest

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access


def test_gate_authenticate_unauthorized(db, caplog):
    _, revoked_key = access.create_credential("client", "revoked-agent")
    cid = access.authenticate(revoked_key, {"client"}).client_id
    access.revoke_client(cid)
    caplog.set_level(logging.DEBUG)
    for bad in ("never-issued-key", revoked_key, None, ""):
        with pytest.raises(access.Unauthorized):
            access.authenticate(bad, {"client", "admin"})
    assert "revoked-agent" in caplog.text
    assert revoked_key not in caplog.text and "never-issued-key" not in caplog.text


def test_gate_authenticate_forbidden(db):
    _, admin_key = access.create_credential("admin", "root")
    _, client_key = access.create_credential("client", "agent")
    with pytest.raises(access.Forbidden):
        access.authenticate(admin_key, {"client"})
    with pytest.raises(access.Forbidden):
        access.authenticate(client_key, {"admin"})


def test_gate_keys_from_os_and_hash_is_stable(db, monkeypatch):
    from qrypt_entropy.upstream import source

    def fail(*args, **kwargs):
        pytest.fail("the entropy source must not be called")

    monkeypatch.setattr(source, "fetch", fail)
    monkeypatch.setattr(source, "request_stats", fail)
    principal, key = access.create_credential("client", "os-keys")
    assert principal.role == "client"
    assert access.hash_key(key) == access.hash_key(key)
    assert access.hash_key(key) != access.hash_key(key + "x")
    assert get_settings().key_hash_secret
