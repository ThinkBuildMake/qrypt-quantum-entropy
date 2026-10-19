"""S28 gate cases: the status report job writes server_status (docs/stories/S28-status-report.md AC1-AC6).

Pool, call and request statistics are stubbed; the database is real.
"""

import asyncio
import json

import psycopg
import pytest

from qrypt_entropy.logs import configure_logging
from qrypt_entropy.models import PoolStatus
from qrypt_entropy.service import status
from qrypt_entropy.service.pool import Pool
from qrypt_entropy.service.status import StatusReporter
from qrypt_entropy.storage import db as storage_db

MARKER = b"\xde\xad\xbe\xef-marker-0123456789"


class Stats:
    """Stub Pool.status, Issuance.callStats and Entropy source.requestStats."""

    def __init__(self):
        self.calls = {}
        self.pool_holds = MARKER  # what a real pool holds; the report must never read it

    def pool(self):
        return PoolStatus(98304, 32768, 131072, "ok", "2026-10-17T10:00:00+00:00")

    def call_stats(self):
        return {
            "callsByTool": dict(self.calls),
            "errorsByCode": {"invalid_argument": 1},
            "toolCallDurationMs": {"p50": 1.5, "p95": 4.0, "p99": 9.0},
        }

    def request_stats(self):
        return {"requestsLast10s": 7}

    def reporter(self, **kw):
        return StatusReporter(self.pool, self.call_stats, self.request_stats, **kw)


@pytest.fixture
def stats(db):
    yield Stats()
    storage_db.close_pools()


def rows(
    url,
    columns="instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, upstream_status, snapshot, reported_at",
):
    with psycopg.connect(url) as conn:
        return conn.execute(f"SELECT {columns} FROM server_status ORDER BY reported_at").fetchall()


async def run_for(reporter, seconds):
    reporter.start()
    await asyncio.sleep(seconds)
    await reporter.stop()


def test_status_every_5s(stats, db):
    reporter = stats.reporter()
    seen = []

    async def go():
        reporter.start()
        for _ in range(11):
            await asyncio.sleep(1)
            seen.extend(r[0] for r in rows(db, "reported_at"))
        await reporter.stop()

    asyncio.run(go())
    final = rows(db)
    assert len(final) == 1
    assert final[0][1:5] == (98304, 32768, 131072, "ok")
    assert len(set(seen)) >= 3  # first report at start, then every 5 s: 0, 5 and 10 s
    assert status.INTERVAL_S == 5


def test_status_no_entropy(stats, db):
    real_pool = Pool(32768, 131072)
    real_pool.add("00000000-0000-4000-8000-0000000000b1", MARKER)
    asyncio.run(StatusReporter(real_pool.status, stats.call_stats, stats.request_stats).report())
    row = rows(db)[0]
    assert row[1] == len(MARKER)  # the pool really held the marker
    text = json.dumps([str(c) for c in row])
    for form in (MARKER.hex(), MARKER.hex().upper(), repr(MARKER), MARKER.decode(errors="ignore")):
        assert form not in text


def test_status_snapshot_shape(stats, db):
    stats.calls = {"get_random_bytes": 2}
    asyncio.run(stats.reporter().report())
    snapshot = rows(db, "snapshot")[0][0]
    assert set(snapshot) == {
        "startedAt",
        "upstreamRequestsLast10s",
        "callsByTool",
        "errorsByCode",
        "toolCallDurationMs",
    }
    assert snapshot["upstreamRequestsLast10s"] == 7
    assert snapshot["callsByTool"] == {"get_random_bytes": 2}
    assert snapshot["errorsByCode"] == {"invalid_argument": 1}
    assert set(snapshot["toolCallDurationMs"]) == {"p50", "p95", "p99"}
    assert snapshot["startedAt"].endswith("+00:00")


def test_status_cumulative(stats, db):
    reporter = stats.reporter()
    stats.calls = {"get_random_bytes": 3}
    asyncio.run(reporter.report())
    first = rows(db, "snapshot")[0][0]["callsByTool"]
    stats.calls = {"get_random_bytes": 5}
    asyncio.run(reporter.report())
    second = rows(db, "snapshot")
    assert len(second) == 1
    assert (first, second[0][0]["callsByTool"]) == ({"get_random_bytes": 3}, {"get_random_bytes": 5})


def test_status_write_fails(stats, db, monkeypatch, tmp_path):
    log_file = tmp_path / "server.log"
    configure_logging("INFO", str(log_file))
    real_write = StatusReporter._write
    refusals = [True]

    def flaky(self, row):
        if refusals:
            refusals.pop()
            stats.calls = {"get_random_bytes": 1}  # a tool call during the failing tick
            raise psycopg.OperationalError("refused")
        real_write(self, row)

    monkeypatch.setattr(StatusReporter, "_write", flaky)
    asyncio.run(run_for(stats.reporter(interval=0.2), 0.7))
    configure_logging()
    events = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert any(e.get("event") == "status_report_failed" and e.get("level") == "error" for e in events)
    written = rows(db)
    assert len(written) == 1  # the next tick wrote, and it saw the call made during the failure
    assert written[0][5]["callsByTool"] == {"get_random_bytes": 1}


def test_status_restart_new_row(stats, db):
    asyncio.run(stats.reporter().report())
    asyncio.run(stats.reporter().report())
    ids = [r[0] for r in rows(db)]
    assert len(ids) == 2 and ids[0] != ids[1]
