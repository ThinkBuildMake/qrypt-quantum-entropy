"""S07 gate cases for Meter.reserve / Meter.mark_batch, against a real Postgres (never Qrypt)."""

import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from qrypt_entropy.errors import BudgetExhausted
from qrypt_entropy.service import meter
from tests.test_migrations import migrate


@pytest.fixture
def db(empty_db):
    assert migrate(empty_db).returncode == 0
    yield empty_db
    meter.close_pools()


def set_budget(url, cap, used=0):
    with psycopg.connect(url) as conn:
        conn.execute("UPDATE budget SET cap_kib = %s, used_kib = %s", (cap, used))


def budget(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT used_kib FROM budget").fetchone()[0]


def batches(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT kib, purpose, outcome, settled_at, source FROM batches").fetchall()


def alerts(caplog):
    events = [json.loads(r.getMessage()) for r in caplog.records if "budget_alert" in r.getMessage()]
    return [e["threshold_percent"] for e in events]


def test_reserve_grants(db):
    batch = meter.reserve(64, "refill", "qrypt", url=db)
    assert batch.kib == 64
    assert budget(db) == 64
    assert batches(db) == [(64, "refill", "requested", None, "qrypt")]


def test_reserve_sized_to_remaining(db):
    set_budget(db, 100, 90)
    assert meter.reserve(64, "refill", "qrypt", url=db).kib == 10
    assert budget(db) == 100


def test_reserve_exhausted(db, caplog):
    set_budget(db, 100, 100)
    with caplog.at_level(logging.INFO), pytest.raises(BudgetExhausted):
        meter.reserve(64, "refill", "qrypt", url=db)
    assert batches(db) == []
    assert alerts(caplog) == [100]


def test_reserve_race_100(db):
    set_budget(db, 1000)

    def call(_):
        try:
            return meter.reserve(64, "refill", "qrypt", url=db).kib
        except BudgetExhausted:
            return 0

    with ThreadPoolExecutor(100) as ex:
        granted = list(ex.map(call, range(100)))
    assert sum(granted) == 1000
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT sum(kib) FROM batches").fetchone()[0] == budget(db) == 1000


LOOP = (
    "import sys\n"
    "from qrypt_entropy.errors import BudgetExhausted\n"
    "from qrypt_entropy.service import meter\n"
    "total = 0\n"
    "while True:\n"
    "    try:\n"
    "        total += meter.reserve(64, 'refill', 'qrypt').kib\n"
    "    except BudgetExhausted:\n"
    "        break\n"
    "print(total)\n"
)


def test_reserve_two_processes(db):
    set_budget(db, 1000)
    env = {**os.environ, "DATABASE_URL": db, "LOG_FILE": ""}
    procs = [
        subprocess.Popen([sys.executable, "-c", LOOP], env=env, stdout=subprocess.PIPE, text=True) for _ in range(2)
    ]
    totals = [int(p.communicate()[0].strip()) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(totals) == 1000
    assert budget(db) == 1000


def test_mark_batch_failed_counts(db):
    batch = meter.reserve(64, "refill", "qrypt", url=db)
    meter.mark_batch(batch.batch_id, "failed", url=db)
    [(kib, _, outcome, settled_at, _)] = batches(db)
    assert (kib, outcome) == (64, "failed")
    assert settled_at is not None
    assert budget(db) == 64


def test_budget_alerts(db, caplog):
    set_budget(db, 100)
    with caplog.at_level(logging.INFO):
        meter.reserve(79, "refill", "qrypt", url=db)
        assert alerts(caplog) == []
        meter.reserve(1, "refill", "qrypt", url=db)
        meter.reserve(10, "refill", "qrypt", url=db)
        meter.reserve(10, "refill", "qrypt", url=db)
        with pytest.raises(BudgetExhausted):
            meter.reserve(5, "refill", "qrypt", url=db)
    assert alerts(caplog) == [80, 100]


def test_reserve_after_cap_lowered(db):
    set_budget(db, 100, 128)
    with pytest.raises(BudgetExhausted):
        meter.reserve(64, "refill", "qrypt", url=db)
    assert budget(db) == 128


def test_reserve_startup_purpose(db):
    assert meter.reserve(1, "startup_check", "qrypt", url=db).kib == 1
    assert batches(db) == [(1, "startup_check", "requested", None, "qrypt")]


def test_meter_survives_restart(db):
    meter.reserve(64, "refill", "qrypt", url=db)
    meter.close_pools()  # the process's connections are gone; a new process opens fresh ones
    code = (
        "import os, psycopg\n"
        "print(psycopg.connect(os.environ['DATABASE_URL']).execute('SELECT used_kib FROM budget').fetchone()[0])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env={**os.environ, "DATABASE_URL": db}, capture_output=True, text=True
    )
    assert int(out.stdout.strip()) >= 64
