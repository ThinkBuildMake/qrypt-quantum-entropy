"""S22 gate cases: five failed redemptions lock an owner out (docs/stories/S22-tok-limit.md AC1-AC5)."""

import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import access, tokens
from qrypt_entropy.storage import db as storage_db
from qrypt_entropy.transport.http_app import app

OWNER, VALUE, WRONG = "user-42", "123456", "000000"


@pytest.fixture
def world(db):
    http = TestClient(app, raise_server_exceptions=False)
    _, key = access.create_credential("client", "redeemer")
    headers = {"Authorization": f"Bearer {key}"}

    def post(owner=OWNER, value=WRONG):
        return http.post("/verify", headers=headers, json={"owner": owner, "value": value})

    yield post, db
    storage_db.close_pools()


def outcomes(url, owner=OWNER):
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT outcome FROM redemption_outcomes WHERE owner = %s ORDER BY attempted_at", (owner,))
        return [r[0] for r in rows.fetchall()]


def fail_five(post):
    assert [post().status_code for _ in range(5)] == [404] * 5


def test_gate_verify_rate_limit(world):
    post, url = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    fail_five(post)
    r = post(value=VALUE)
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited" and r.json()["outcome"] == "rate_limited"
    assert outcomes(url)[-1] == "rate_limited" and len(outcomes(url)) == 6


def test_gate_verify_rate_limit_window(world, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "3")
    get_settings.cache_clear()
    post, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    fail_five(post)
    assert post(value=VALUE).status_code == 429
    time.sleep(3.2)
    assert post(value=VALUE).status_code == 200


def test_gate_verify_rate_limit_per_owner(world):
    post, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    tokens.store_seed_token("user-43", "otp_6", VALUE, 600)
    fail_five(post)
    assert post(value=VALUE).status_code == 429
    assert post("user-43", VALUE).status_code == 200


def test_gate_verify_rate_limit_concurrent(world):
    post, url = world
    with ThreadPoolExecutor(max_workers=50) as pool:
        codes = [f.result().status_code for f in [pool.submit(post) for _ in range(50)]]
    assert codes.count(404) == 5 and codes.count(429) == 45
    rows = outcomes(url)
    assert rows.count("unknown") == 5 and rows.count("rate_limited") == 45


def test_gate_verify_success_not_failure(world):
    post, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    assert [post().status_code for _ in range(4)] == [404] * 4
    assert post(value=VALUE).status_code == 200
    assert post().status_code == 404


def test_gate_expired_and_already_used_count_as_failures(world):
    post, _ = world
    tokens.store_seed_token("exp-owner", "otp_6", VALUE, 1)
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    assert post(OWNER, VALUE).status_code == 200
    time.sleep(1.5)
    assert [post("exp-owner", VALUE).status_code for _ in range(5)] == [410] * 5
    assert post("exp-owner", VALUE).status_code == 429
    assert [post(OWNER, VALUE).status_code for _ in range(5)] == [409] * 5
    assert post(OWNER, VALUE).status_code == 429


def test_gate_rate_limited_attempts_do_not_extend_the_lockout(world, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "3")
    get_settings.cache_clear()
    post, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    start = time.monotonic()
    fail_five(post)
    for pause in (1.0, 2.0):
        time.sleep(max(0, start + pause - time.monotonic()))
        assert post(value=VALUE).status_code == 429
    time.sleep(max(0, start + 3.3 - time.monotonic()))
    assert post(value=VALUE).status_code == 200


def test_gate_flood_for_one_owner_gets_503_not_a_stuck_connection(world, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOCK_TIMEOUT_MS", "300")
    get_settings.cache_clear()
    post, url = world
    with psycopg.connect(url) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (OWNER,))
        begun = time.monotonic()
        r = post()
        assert 0.25 < time.monotonic() - begun < 2
    assert (r.status_code, r.json()["error"]) == (503, "temporarily_unavailable")
    assert outcomes(url) == []
    assert post().status_code == 404
