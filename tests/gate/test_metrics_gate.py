"""S30 gate cases: GET /metrics (`metrics-parse`, `metrics-values`), series presence, staleness, access."""

import json
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from qrypt_entropy.service import access, meter
from qrypt_entropy.transport import metrics_sampler
from qrypt_entropy.transport.http_app import app

SNAPSHOT = {
    "startedAt": "2026-10-18T10:00:00+00:00",
    "upstreamRequestsLast10s": 4,
    "callsByTool": {"get_random_bytes": 7, "random_int": 2},
    "errorsByCode": {"invalid_argument": 3},
    "toolCallDurationMs": {"p50": 1.5, "p95": 9.0, "p99": 20.0},
}


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


def seed(db, age_s=1, snapshot=SNAPSHOT, used_kib=640):
    with psycopg.connect(db) as conn:
        conn.execute("UPDATE budget SET used_kib = %s", (used_kib,))
        conn.execute(
            "INSERT INTO server_status (instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, "
            "upstream_status, snapshot, reported_at) "
            "VALUES (gen_random_uuid(), 50000, 32768, 131072, 'ok', %s, now() - make_interval(secs => %s))",
            (json.dumps(snapshot), age_s),
        )


def samples(text):
    return {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for family in text_string_to_metric_families(text)
        for s in family.samples
    }


def test_ac1_ac2_series_present_and_parse(http, keys, db):  # gate: metrics-parse
    seed(db)
    r = http.get("/metrics", headers=keys["admin"])
    assert r.status_code == 200
    got = samples(r.text)  # parsing raises on malformed text
    names = {n for n, _ in got}
    assert {
        "qrypt_pool_level_bytes",
        "qrypt_upstream_requests_last_10s",
        "qrypt_budget_used_kib",
        "qrypt_calls_total",
        "qrypt_errors_total",
        "qrypt_tool_call_duration_ms",
        "qrypt_report_age_seconds",
        "qrypt_process_start_time_seconds",
    } <= names
    assert {q for n, lbl in got if n == "qrypt_tool_call_duration_ms" for _, q in lbl} == {"0.5", "0.95", "0.99"}
    assert got[("qrypt_pool_level_bytes", ())] == 50000
    assert got[("qrypt_budget_used_kib", ())] == 640


def test_ac3_stale_report_drops_live_series(http, keys, db):
    seed(db, age_s=16)
    got = samples(http.get("/metrics", headers=keys["admin"]).text)
    names = {n for n, _ in got}
    assert "qrypt_report_age_seconds" in names and got[("qrypt_report_age_seconds", ())] >= 16
    assert not names & {"qrypt_pool_level_bytes", "qrypt_calls_total", "qrypt_errors_total"}


def test_no_report_row_still_answers(http, keys):
    r = http.get("/metrics", headers=keys["admin"])
    assert r.status_code == 200 and "qrypt_report_age_seconds" in r.text


def test_ac4_values_match_snapshot(http, keys, db):  # gate: metrics-values
    seed(db)
    got = samples(http.get("/metrics", headers=keys["admin"]).text)
    assert got[("qrypt_calls_total", (("tool", "get_random_bytes"),))] == 7
    assert got[("qrypt_errors_total", (("code", "invalid_argument"),))] == 3
    assert got[("qrypt_upstream_requests_last_10s", ())] == 4
    assert got[("qrypt_tool_call_duration_ms", (("quantile", "0.95"),))] == 9.0
    started = datetime(2026, 10, 18, 10, tzinfo=UTC).timestamp()
    assert got[("qrypt_process_start_time_seconds", ())] == started


def test_the_latest_report_wins(http, keys, db):
    seed(db, age_s=5, snapshot={**SNAPSHOT, "callsByTool": {"get_random_bytes": 1}})
    seed(db, age_s=1)
    got = samples(http.get("/metrics", headers=keys["admin"]).text)
    assert got[("qrypt_calls_total", (("tool", "get_random_bytes"),))] == 7


def test_the_sampler_reads_what_is_emitted(http, keys, db):
    seed(db)
    snap = metrics_sampler.to_snapshot(http.get("/metrics", headers=keys["admin"]).text, datetime.now(UTC))
    assert snap["callsByTool"]["get_random_bytes"] == 7
    assert snap["errorsByCode"] == {"invalid_argument": 3}
    assert snap["upstreamRequestsLast10s"] == 4 and snap["startedAt"]


def test_ac5_client_key_and_no_key_are_refused(http, keys, db):
    seed(db)
    assert http.get("/metrics", headers=keys["client"]).status_code == 403
    assert http.get("/metrics", headers=keys["client"]).json()["error"] == "forbidden"
    r = http.get("/metrics")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
