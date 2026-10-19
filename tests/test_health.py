"""S29 AC1-AC5: GET /health from seeded server_status rows."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from qrypt_entropy.storage import db as storage_db
from qrypt_entropy.transport.http_app import app


@pytest.fixture
def world(db):
    yield TestClient(app, raise_server_exceptions=False), db
    storage_db.close_pools()


def seed(url, age_s, level=98304, status="ok", last_success_age_s=5):
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO server_status (instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, "
            "upstream_status, last_success_at, snapshot, reported_at) VALUES "
            "(%s, %s, 32768, 131072, %s, now() - make_interval(secs => %s), '{}', now() - make_interval(secs => %s))",
            (uuid.uuid4(), level, status, last_success_age_s, age_s),
        )


def test_ac1_fresh_report_shows_pool_and_upstream(world):
    http, url = world
    seed(url, 3)
    r = http.get("/health")  # no key
    body = r.json()
    assert r.status_code == 200
    assert body["reporting"] is True
    assert body["reportAgeSeconds"] == pytest.approx(3, abs=1)
    assert body["pool"] == {"levelBytes": 98304, "lowMarkBytes": 32768, "highMarkBytes": 131072}
    assert body["upstream"]["status"] == "ok"
    last = datetime.fromisoformat(body["upstream"]["lastSuccessAt"])
    assert (datetime.now(UTC) - last).total_seconds() == pytest.approx(5, abs=2)


def test_ac1_upstream_without_a_success_has_null_last_success(world):
    http, url = world
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO server_status (instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, "
            "upstream_status, snapshot) VALUES (%s, 0, 1, 2, 'failing', '{}')",
            (uuid.uuid4(),),
        )
    assert http.get("/health").json()["upstream"] == {"status": "failing", "lastSuccessAt": None}


def test_ac2_old_report_reads_as_not_reporting(world):
    http, url = world
    seed(url, 16)
    assert http.get("/health").json() == {"reporting": False, "reportAgeSeconds": None, "pool": None, "upstream": None}


def test_ac3_no_row_is_an_empty_state(world):
    http, _ = world
    r = http.get("/health")
    assert r.status_code == 200
    assert r.json() == {"reporting": False, "reportAgeSeconds": None, "pool": None, "upstream": None}


def test_ac4_most_recent_row_is_used(world):
    http, url = world
    seed(url, 40, level=111)
    seed(url, 2, level=222)
    assert http.get("/health").json()["pool"]["levelBytes"] == 222


def test_ac5_misconfigured_upstream_is_shown(world):
    http, url = world
    seed(url, 1, status="misconfigured")
    assert http.get("/health").json()["upstream"]["status"] == "misconfigured"


@pytest.mark.parametrize(("age", "reporting"), [(4, True), (14, True), (14.9, True), (16, False), (60, False)])
def test_staleness_boundary_is_15_seconds(world, age, reporting):
    http, url = world
    seed(url, age)
    body = http.get("/health").json()
    assert body["reporting"] is reporting
    assert (body["pool"] is not None) is reporting


def test_future_report_is_fresh_with_age_zero(world):
    http, url = world
    seed(url, -30)
    body = http.get("/health").json()
    assert body["reporting"] is True
    assert body["reportAgeSeconds"] == 0


def test_request_log_says_what_the_client_got_for_an_unhandled_error(monkeypatch, tmp_path):
    """H03: an unhandled exception is answered 503 by the crash handler; the request line must say 503, not 500."""
    import json

    from fastapi.testclient import TestClient

    from qrypt_entropy.logs import configure_logging
    from qrypt_entropy.service import status
    from qrypt_entropy.transport.http_app import app

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "get_health", boom)
    log_file = tmp_path / "http.log"
    configure_logging("INFO", str(log_file))
    try:
        reply = TestClient(app, raise_server_exceptions=False).get("/health")
    finally:
        lines = [json.loads(line) for line in log_file.read_text().splitlines()]
        configure_logging()
    logged = [line for line in lines if line.get("event") == "request" and line.get("path") == "/health"]
    assert reply.status_code == 503 and [line["status"] for line in logged] == [503]
