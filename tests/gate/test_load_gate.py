"""S36 gate cases: a short run of the committed load client (scripts/load_test.py) against the real server over stdio.

The full runs are `make load-latency` and `make load-concurrent` (about 15 s and 50 s); the gate runs the same code
with fewer calls and callers, so every check in the client (p95, errors, DE-1 window, 64 KiB per request, pool marks,
overlap, audit rows) is exercised on each gate run. The numbers it writes are scratch, not the committed results.
"""

import json
import subprocess
import sys

from tests.conftest import GATE_STATS, base_url


def run_load(tmp_path, *args):
    out = tmp_path / "out.json"
    done = subprocess.run(
        [sys.executable, "scripts/load_test.py", *args, "--out", str(out)],
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": base_url(), "VIRTUAL_ENV": sys.prefix},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stdout + done.stderr[-2000:]
    return json.loads(out.read_text())


def test_load_latency_smoke(tmp_path):
    """AC1 on a short run: the client reports p50, p95, p99 and the database, and p95 stays under the limit."""
    doc = run_load(tmp_path, "latency", "--calls", "150")
    assert doc["calls"] == 150 and doc["errors"] == 0
    assert doc["p50_ms"] <= doc["p95_ms"] <= doc["p99_ms"]
    assert "PostgreSQL" in doc["database_used"]
    GATE_STATS["load smoke p95 (ms) over 150 calls"] = f"{doc['p95_ms']}"


def test_load_concurrent_smoke(tmp_path):
    """AC2 to AC7 on a short run: 20 callers, 4 s, at least 600 calls."""
    doc = run_load(tmp_path, "concurrent", "--callers", "20", "--seconds", "4", "--min-calls", "600", "--smoke")
    assert doc["server_error_rate"] == 0 and doc["server_errors_by_code"] == {}
    sent = doc["injected_invalid_calls"]
    assert sent["sent"] > 0 and sent["sent"] == sent["answered_invalid_argument"]
    assert doc["calls"] == doc["successful_calls"] + sent["sent"]
    assert doc["kib_fetched_per_upstream_request"] >= 64
    assert doc["max_upstream_requests_in_10s"] <= 30
    assert doc["pool_max_level_kib"] <= 128
    n = doc[
        "upstream_requests_excluding_startup_check"
    ]  # served can fall short of fetched only by what is still pooled
    assert 96 * n - 129 <= doc["kib_served_per_upstream_request"] * n <= doc["kib_fetched_per_upstream_request"] * n
    assert doc["overlapping_ranges"] == 0
    assert doc["issuances_rows"] == doc["successful_calls"] + doc["warm_up_calls"] and doc["warm_up_calls"] >= 1
    GATE_STATS["load smoke calls, max upstream requests in 10 s"] = (
        f"{doc['calls']}, {doc['max_upstream_requests_in_10s']}"
    )
