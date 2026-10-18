"""S29 gate cases: health-db-down (AC6) and health-latency (AC7)."""

import statistics
import time
import uuid

import psycopg
from fastapi.testclient import TestClient

from qrypt_entropy.config import get_settings
from qrypt_entropy.storage import db as storage_db
from qrypt_entropy.transport.http_app import app
from tests.conftest import GATE_STATS

DEAD_URL = "postgresql://dev:dev@127.0.0.1:1/none"  # nothing listens on port 1: the database is "stopped"
P95_LIMIT_MS = 200  # OB-1


def test_gate_health_db_down(db, monkeypatch):
    http = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    get_settings.cache_clear()
    try:
        r = http.get("/health")
    finally:
        storage_db.close_pools()
        get_settings.cache_clear()
    assert r.status_code == 503
    assert r.json()["error"] == "database_unavailable"


def test_gate_health_latency(db):
    http = TestClient(app)
    with psycopg.connect(db) as conn:
        conn.execute(
            "INSERT INTO server_status (instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, "
            "upstream_status, snapshot) VALUES (%s, 98304, 32768, 131072, 'ok', '{}')",
            (uuid.uuid4(),),
        )
    assert http.get("/health").json()["reporting"] is True  # warm the connection pool
    durations = []
    for _ in range(100):
        started = time.perf_counter()
        assert http.get("/health").status_code == 200
        durations.append((time.perf_counter() - started) * 1000)
    storage_db.close_pools()
    p95 = statistics.quantiles(durations, n=100)[94]
    GATE_STATS["health p95 (ms) over 100 calls, limit"] = f"{p95:.2f} <= {P95_LIMIT_MS}"
    assert p95 <= P95_LIMIT_MS
