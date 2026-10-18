"""Status: what the hosted service can say about the MCP server. Owns `server_status`.

S01 STUB: `report_once` (S28 writes `server_status`). `get_health` (S29) and `get_metrics` (S30) are real.
"""

from datetime import datetime

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from qrypt_entropy.errors import DatabaseUnavailable, TemporarilyUnavailable
from qrypt_entropy.storage import db
from qrypt_entropy.stubs import stub

STALE_AFTER_SECONDS = 15  # a report older than this reads as "not reporting": three missed 5 s reports (OB-1)
NOT_REPORTING = {"reporting": False, "reportAgeSeconds": None, "pool": None, "upstream": None}


def get_health() -> dict:
    """The most recent report as health; older than 15 s or absent reads as not reporting (database clock)."""
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT extract(epoch FROM now() - reported_at), pool_level_bytes, low_mark_bytes, high_mark_bytes, "
                "upstream_status, last_success_at FROM server_status ORDER BY reported_at DESC LIMIT 1"
            ).fetchone()
    except TemporarilyUnavailable as err:
        raise DatabaseUnavailable("the database is unavailable") from err
    if row is None or row[0] > STALE_AFTER_SECONDS:
        return dict(NOT_REPORTING)
    age, level, low, high, upstream_status, last_success = row
    return {
        "reporting": True,
        "reportAgeSeconds": max(0.0, round(float(age), 1)),
        "pool": {"levelBytes": level, "lowMarkBytes": low, "highMarkBytes": high},
        "upstream": {"status": upstream_status, "lastSuccessAt": last_success.isoformat() if last_success else None},
    }


def _epoch(started_at) -> float | None:
    """The snapshot's `startedAt` as epoch seconds; accepts a number or an ISO-8601 string."""
    if isinstance(started_at, int | float):
        return float(started_at)
    try:
        return datetime.fromisoformat(started_at).timestamp()
    except TypeError, ValueError:
        return None


class _Series:
    """The latest report and the budget, as Prometheus series. Live series only when the server is reporting."""

    def __init__(self, row, used_kib: int) -> None:
        self.row, self.used_kib = row, used_kib

    def collect(self):
        age = self.row[0] if self.row else float("nan")
        report_age = GaugeMetricFamily("qrypt_report_age_seconds", "Age of the last server report", value=age)
        yield report_age
        yield GaugeMetricFamily("qrypt_budget_used_kib", "KiB requested from the entropy source", value=self.used_kib)
        if not self.row or age > STALE_AFTER_SECONDS:
            return
        _, level, snap = self.row
        yield GaugeMetricFamily("qrypt_pool_level_bytes", "Bytes held in the pool", value=level)
        yield GaugeMetricFamily(
            "qrypt_upstream_requests_last_10s",
            "Requests to the entropy source in the last 10 s",
            value=snap.get("upstreamRequestsLast10s", 0),
        )
        started = _epoch(snap.get("startedAt"))
        if started is not None:
            yield GaugeMetricFamily("qrypt_process_start_time_seconds", "Server start, epoch seconds", value=started)
        calls = CounterMetricFamily("qrypt_calls", "Tool calls since start", labels=["tool"])
        for tool, n in sorted(snap.get("callsByTool", {}).items()):
            calls.add_metric([tool], n)
        yield calls
        errors = CounterMetricFamily("qrypt_errors", "Errors since start", labels=["code"])
        for code, n in sorted(snap.get("errorsByCode", {}).items()):
            errors.add_metric([code], n)
        yield errors
        durations = GaugeMetricFamily(
            "qrypt_tool_call_duration_ms", "Tool-call duration since start", labels=["quantile"]
        )
        for q, key in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99")):
            durations.add_metric([q], snap.get("toolCallDurationMs", {}).get(key, 0))
        yield durations


def get_metrics(*, url: str | None = None) -> str:
    """Prometheus text from the most recently reporting `server_status` row and the budget (OB-4)."""
    with db.connection(url) as conn:
        row = conn.execute(
            "SELECT extract(epoch FROM now() - reported_at)::float8, pool_level_bytes, snapshot "
            "FROM server_status ORDER BY reported_at DESC LIMIT 1"
        ).fetchone()
        (used_kib,) = conn.execute("SELECT used_kib FROM budget WHERE id = 1").fetchone()
    registry = CollectorRegistry()
    registry.register(_Series(row, used_kib))
    return generate_latest(registry).decode()


@stub("S28")
async def report_once() -> None:
    return None
