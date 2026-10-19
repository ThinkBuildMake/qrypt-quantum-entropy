"""S21 gate cases: redemption through POST /verify (docs/stories/S21-tok-redeem.md AC1-AC8)."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.service import access, tokens
from qrypt_entropy.storage import db as storage_db
from qrypt_entropy.transport.http_app import app

OWNER, VALUE = "user-42", "123456"
BODY = {"owner": OWNER, "value": VALUE}


@pytest.fixture
def world(db):
    http = TestClient(app, raise_server_exceptions=False)
    _, client_key = access.create_credential("client", "redeemer")
    _, admin_key = access.create_credential("admin", "root")
    yield http, {"Authorization": f"Bearer {client_key}"}, {"Authorization": f"Bearer {admin_key}"}, db
    storage_db.close_pools()


def outcomes(url):
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT outcome, owner, token_id, attempted_at FROM redemption_outcomes ORDER BY attempted_at"
        ).fetchall()


def test_gate_valid_code_redeems_once_then_refuses(world):
    http, client, _, url = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    first = http.post("/verify", headers=client, json=BODY)
    assert (first.status_code, first.json()) == (200, {"outcome": "redeemed"})
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT used_at IS NOT NULL FROM tokens").fetchone() == (True,)
    second = http.post("/verify", headers=client, json=BODY)
    assert second.status_code == 409
    assert second.json()["error"] == "already_used" and second.json()["outcome"] == "already_used"


def test_gate_verify_expired(world):
    http, client, _, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 2)
    time.sleep(3)
    r = http.post("/verify", headers=client, json=BODY)
    assert r.status_code == 410
    assert r.json()["error"] == "expired" and r.json()["outcome"] == "expired"


def test_gate_wrong_value_is_unknown_without_token_id(world):
    http, client, _, url = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    r = http.post("/verify", headers=client, json={"owner": OWNER, "value": "000000"})
    assert r.status_code == 404
    assert r.json()["error"] == "unknown" and r.json()["outcome"] == "unknown"
    assert [(o[0], o[2]) for o in outcomes(url)] == [("unknown", None)]


def test_gate_verify_race_1000(world):
    http, client, _, url = world
    with psycopg.connect(url) as conn:  # one transaction for all 1000 fresh tokens
        for trial in range(1000):
            tokens._insert(conn, None, f"racer-{trial}", "otp_6", VALUE, 600)  # noqa: SLF001
    wins = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for trial in range(1000):
            owner = f"racer-{trial}"
            barrier = threading.Barrier(2)

            def attempt(owner=owner, barrier=barrier):
                barrier.wait()
                return http.post("/verify", headers=client, json={"owner": owner, "value": VALUE}).status_code

            codes = sorted(f.result() for f in [pool.submit(attempt), pool.submit(attempt)])
            wins.append(codes)
    assert all(codes == [200, 409] for codes in wins), [c for c in wins if c != [200, 409]][:5]


def test_gate_outcomes_recorded_no_value(world):
    http, client, _, url = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    tokens.store_seed_token("slow-user", "otp_6", "777777", 1)
    tokens.store_seed_token("other-user", "otp_6", "424242", 600)
    time.sleep(2)
    sent = [(OWNER, VALUE), (OWNER, VALUE), ("slow-user", "777777"), ("other-user", "999999")]
    for owner, value in sent:
        http.post("/verify", headers=client, json={"owner": owner, "value": value})
    rows = outcomes(url)
    assert sorted((r[0], r[1]) for r in rows) == sorted(
        [("redeemed", OWNER), ("already_used", OWNER), ("expired", "slow-user"), ("unknown", "other-user")]
    )
    assert len(rows) == 4 and all(abs(time.time() - r[3].timestamp()) < 30 for r in rows)
    with psycopg.connect(url) as conn:
        dump = json.dumps([conn.execute(f"SELECT {t}::text FROM {t}").fetchall() for t in ("redemption_outcomes",)])
    assert not any(v in dump for _, v in sent)


def test_gate_verify_invalid(world):
    http, client, _, url = world
    missing = http.post("/verify", headers=client, json={"owner": OWNER})
    long_owner = http.post("/verify", headers=client, json={"owner": "o" * 257, "value": VALUE})
    anonymous = http.post("/verify", json=BODY)
    assert (missing.status_code, long_owner.status_code, anonymous.status_code) == (400, 400, 401)
    assert http.post("/verify", headers=client, json={"owner": "a\x00b", "value": VALUE}).status_code == 400
    assert outcomes(url) == []


def test_gate_verify_admin_and_duplicates(world):
    http, _, admin, _ = world
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    tokens.store_seed_token(OWNER, "otp_6", VALUE, 600)
    codes = [http.post("/verify", headers=admin, json=BODY).status_code for _ in range(3)]
    assert codes == [200, 200, 409]
