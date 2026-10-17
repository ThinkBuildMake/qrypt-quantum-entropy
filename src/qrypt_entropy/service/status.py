"""Status: what the hosted service can say about the MCP server. Owns `server_status`.

S01 STUB: synthetic health and metrics, no database read. Real: S28 (report job), S29 (/health), S30 (/metrics).
"""

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from qrypt_entropy.stubs import stub


@stub("S29")
def get_health() -> dict:
    return {
        "reporting": True,
        "reportAgeSeconds": 1.0,
        "pool": {"levelBytes": 65536, "lowMarkBytes": 32768, "highMarkBytes": 131072},
        "upstream": {"status": "ok", "lastSuccessAt": None},
    }


@stub("S30")
def get_metrics() -> str:
    registry = CollectorRegistry()
    Gauge("qrypt_pool_level_bytes", "Bytes in the pool (stub)", registry=registry).set(65536)
    Gauge("qrypt_budget_used_kib", "KiB requested upstream (stub)", registry=registry).set(0)
    Gauge("qrypt_report_age_seconds", "Age of the last server report (stub)", registry=registry).set(1)
    return generate_latest(registry).decode()


@stub("S28")
async def report_once() -> None:
    return None
