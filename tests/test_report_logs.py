"""S19: log- and snapshot-fed report panels, and the metrics sampler."""

import http.server
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from qrypt_entropy.service import report_logs
from qrypt_entropy.transport import metrics_sampler

T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def jl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def snap(seconds, errors, requests=0):
    return {
        "timestamp": (T0 + timedelta(seconds=seconds)).isoformat(),
        "errorsByCode": errors,
        "upstreamRequestsLast10s": requests,
    }


def event(seconds, name, **fields):
    return {"timestamp": (T0 + timedelta(seconds=seconds)).isoformat(), "level": "info", "event": name, **fields}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("LOG", raising=False)
    monkeypatch.delenv("SNAPSHOTS", raising=False)
    return monkeypatch


def test_ac1_p95_of_1000_calls(env, tmp_path):
    env.setenv(
        "LOG", jl(tmp_path / "calls.log", [event(i, "tool_call", tool="x", duration_ms=i + 1) for i in range(1000)])
    )
    assert report_logs.tool_call_p95() == "950 ms over 1000 calls (above the 50 ms threshold)"


def test_p95_within_threshold_and_junk_lines_ignored(env, tmp_path):
    path = tmp_path / "calls.log"
    rows = [json.dumps(event(i, "tool_call", duration_ms=5)) for i in range(20)]
    path.write_text("\n".join(["not json", *rows, json.dumps(event(1, "other", duration_ms=9999))]))
    env.setenv("LOG", str(path))
    assert report_logs.tool_call_p95() == "5 ms over 20 calls (within the 50 ms threshold)"


def test_ac2_server_errors_exclude_client_faults(env, tmp_path):
    before = {"invalid_argument": 5, "temporarily_unavailable": 0, "unauthorized": 1}
    after = {"invalid_argument": 9, "temporarily_unavailable": 3, "unauthorized": 2}
    env.setenv("SNAPSHOTS", jl(tmp_path / "snaps.json", [snap(0, before), snap(30, after)]))
    assert report_logs.server_caused_errors() == "3 in the worst 30 s window (above the limit of 0)"


def test_errors_window_is_30s_and_survives_restart(env, tmp_path):
    rows = [snap(0, {"x": 10}), snap(10, {"x": 50}), snap(40, {"x": 51}), snap(70, {"x": 1})]
    env.setenv("SNAPSHOTS", jl(tmp_path / "s.json", rows))
    # windows: t40 vs t10 = 1, t70 vs t40 (counter reset) = 1; the jump before t10 is outside any 30 s window pair
    assert report_logs.server_caused_errors().startswith("1 in")


def test_errors_none_is_within_limit(env, tmp_path):
    env.setenv("SNAPSHOTS", jl(tmp_path / "s.json", [snap(0, {}), snap(5, {"unauthorized": 4})]))
    assert report_logs.server_caused_errors() == "0 in the worst 30 s window (within the limit of 0)"


def test_ac3_worst_10_seconds(env, tmp_path):
    env.setenv("SNAPSHOTS", jl(tmp_path / "s.json", [snap(0, {}, 12), snap(5, {}, 30), snap(10, {}, 7)]))
    assert report_logs.worst_10_seconds() == "2026-10-17: 30 requests (within the limit of 30)"
    env.setenv("SNAPSHOTS", jl(tmp_path / "s.json", [snap(0, {}, 31), snap(86400, {}, 2)]))
    assert "2026-10-17: 31 requests (above the limit of 30); 2026-10-18: 2 requests" in report_logs.worst_10_seconds()


def test_snapshots_may_be_a_json_array(env, tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps([snap(0, {}, 4)]))
    env.setenv("SNAPSHOTS", str(path))
    assert report_logs.worst_10_seconds().startswith("2026-10-17: 4 requests")


def test_ac4_unbacked_replies_per_day(env, tmp_path):
    rows = [event(i, "reply_check", found=3, backed=3) for i in range(8)]
    rows += [event(9, "reply_check", found=2, backed=1), event(10, "reply_check", found=1, backed=0)]
    next_day = event(86400 + 5, "reply_check", found=1, backed=1)
    env.setenv("LOG", jl(tmp_path / "agent.log", [*rows, next_day]))
    assert report_logs.unbacked_replies() == "2026-10-17: 2 of 10 replies; 2026-10-18: 0 of 1 replies"


def test_ac5_time_to_unavailable_uses_waited_s_in_s11_format(env, tmp_path):
    rows = [
        event(0, "pool_cannot_cover", wanted_bytes=64, level_bytes=10),  # covered by a refill: no error event
        event(100, "pool_cannot_cover", wanted_bytes=64, level_bytes=10),
        event(101.4, "pool_error_returned", reason="temporarily_unavailable", wanted_bytes=64, waited_s=1.4),
    ]
    env.setenv("LOG", jl(tmp_path / "calls.log", rows))
    assert report_logs.time_to_unavailable() == "2026-10-17: 1.4 s worst of 1 (within the 2 s limit)"
    env.setenv("LOG", jl(tmp_path / "calls.log", [event(5, "pool_error_returned", waited_s=2.5)]))
    assert "above the 2 s limit" in report_logs.time_to_unavailable()
    env.setenv(
        "LOG",
        jl(
            tmp_path / "calls.log",
            [event(5, "pool_error_returned", waited_s=1.0), event(86400, "pool_error_returned", waited_s=0.5)],
        ),
    )
    assert report_logs.time_to_unavailable().count("worst of 1") == 2


def test_restart_resets_counters_by_started_at(env, tmp_path):
    first = {**snap(0, {"temporarily_unavailable": 10}), "startedAt": "A"}
    restarted = {**snap(30, {"temporarily_unavailable": 12}), "startedAt": "B"}  # not lower, but a new process
    env.setenv("SNAPSHOTS", jl(tmp_path / "s.json", [first, restarted]))
    assert report_logs.server_caused_errors().startswith("12 in")  # 12 new errors, not 12 - 10


def test_missing_inputs_read_no_data(env, tmp_path):
    env.setenv("LOG", str(tmp_path / "absent.log"))
    for panel in (report_logs.tool_call_p95, report_logs.unbacked_replies, report_logs.time_to_unavailable):
        assert panel() == "no data"
    assert report_logs.server_caused_errors() == report_logs.worst_10_seconds() == "no data"


def run_report(db_url, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("LOG", "SNAPSHOTS")}
    env.update(DATABASE_URL=db_url, **extra)
    return subprocess.run(
        [sys.executable, "-m", "qrypt_entropy.transport.report_cli"], env=env, capture_output=True, text=True
    )


def test_cli_shows_panels_and_ac7_no_input_is_no_data(empty_db, tmp_path):
    from tests.test_migrations import migrate

    migrate(empty_db)
    bare = run_report(empty_db)
    assert bare.returncode == 0, bare.stderr
    assert all(ln.endswith("no data") for ln in bare.stdout.splitlines() if ln.startswith("PANEL"))
    log = jl(tmp_path / "calls.log", [event(i, "tool_call", duration_ms=i + 1) for i in range(1000)])
    out = run_report(empty_db, LOG=log).stdout
    assert "tool-call p95 [server log (LOG)]: 950 ms" in out and "above the 50 ms threshold" in out


class _Metrics(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = (
            "# TYPE qrypt_errors_total counter\n"
            f'qrypt_errors_total{{code="temporarily_unavailable"}} {self.hits}\n'
            "# TYPE qrypt_upstream_requests_last_10s gauge\n"
            f"qrypt_upstream_requests_last_10s {self.hits * 2}\n"
        ).encode()
        self.send_response(200 if self.headers.get("Authorization") == "Bearer k" else 401)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_ac6_sampler_appends_snapshots_the_report_reads(env, tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Metrics)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tmp_path / "snaps.json"
    try:
        url = f"http://127.0.0.1:{server.server_port}/metrics"
        assert metrics_sampler.sample(url, "k", seconds=1.2, interval=0.5, out=str(out)) >= 2
    finally:
        server.shutdown()
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) >= 2 and all("timestamp" in r and "errorsByCode" in r for r in rows)
    env.setenv("SNAPSHOTS", str(out))
    assert report_logs.worst_10_seconds().count("requests (")
    assert report_logs.server_caused_errors().startswith(f"{len(rows) - 1} in")
