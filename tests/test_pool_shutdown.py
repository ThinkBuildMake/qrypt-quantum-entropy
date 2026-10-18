"""S38: shutting the process pool down waits for cancelled reservations, so none touches the database afterwards
(a late settle reopened a connection pool that was then finalized at interpreter exit as plain-text noise)."""

import asyncio
import sys
import threading
import time

from qrypt_entropy import logs
from qrypt_entropy.models import Batch
from qrypt_entropy.service import meter, pool


async def test_shutdown_waits_for_a_cancelled_reservation_to_settle(monkeypatch):
    settled, release = [], threading.Event()

    def slow_reserve(kib, purpose, source):
        release.wait(5)
        return Batch("batch-1", kib)

    monkeypatch.setattr(meter, "reserve", slow_reserve)
    monkeypatch.setattr(meter, "mark_batch", lambda batch_id, outcome: (time.sleep(0.2), settled.append(batch_id)))
    p = pool.Pool()
    pool.use_pool(p)
    task = asyncio.create_task(p._reserve(1, "refill"))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    threading.Timer(0.2, release.set).start()
    await pool.shutdown()
    assert settled == ["batch-1"], "shutdown returned while a worker could still write to the database"


def test_an_unraisable_exception_is_one_json_line(capsys):
    logs.configure_logging()
    sys.unraisablehook(type("Args", (), {"exc_value": RuntimeError("secret detail"), "exc_type": RuntimeError})())
    err = capsys.readouterr().err.strip()
    assert err.count("\n") == 0 and '"error": "RuntimeError"' in err and "secret detail" not in err
