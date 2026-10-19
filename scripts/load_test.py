"""Scripted load and latency client (S36; PF-1, PF-2, PF-3, DE-1, SC-1, VR-1, VR-9).

  python scripts/load_test.py latency    [--calls 1000]                          -> results/load_latency.json
  python scripts/load_test.py concurrent [--seconds 30 --callers 100 --min-calls 10000] -> results/load_concurrent.json

Each run builds its own throwaway database on the server DATABASE_URL points at (migrated, seeded, dropped at the end),
starts the committed mock Qrypt (200 ms added latency, 30 requests per 10 s enforced) in a thread of this process, and
drives the real MCP server (scripts/load_server.py, which runs the production `main` plus a pool-level probe) over
stdio. The test database raises the budget cap (the 10,240 KiB cap would run out within a long run; the cap is not
what is being measured). Exit code 1 if a check fails. The numbers and the environment go to the results file.
"""

import argparse
import asyncio
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `tests.mock_qrypt`

import psycopg  # noqa: E402
from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from qrypt_entropy.service.pool import REFILL_KIB  # noqa: E402
from tests.mock_qrypt import LIMIT, WINDOW_S, MockQrypt  # noqa: E402

MOCK_LATENCY_S = 0.2
TEST_CAP_KIB = 100_000_000  # the test database's cap: large enough that the run never reaches it
CALLS = [  # mixed evenly across the three tools; every call asks for 64 bytes or fewer (PF-1)
    ("get_random_bytes", {"length": 64, "encoding": "hex"}),
    ("random_int", {"min": 1, "max": 1000}),
    ("generate_token", {"kind": "uuid_v4"}),
]
INVALID = ("get_random_bytes", {"length": 0, "encoding": "hex"})  # 1 call in 50 in the concurrent run
CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str) -> None:
    CHECKS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}", flush=True)


def run_mock() -> MockQrypt:
    mock, ready = MockQrypt(token="load-" + secrets.token_hex(8)), threading.Event()
    mock.delay_s = MOCK_LATENCY_S

    async def serve() -> None:
        async with mock:
            ready.set()
            await asyncio.Event().wait()

    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    assert ready.wait(10)
    return mock


def percentile(sorted_ms: list[float], q: float) -> float:
    return round(sorted_ms[min(len(sorted_ms) - 1, int(q * len(sorted_ms)))], 2)


def environment(db_url: str) -> dict:
    with psycopg.connect(db_url) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
    cpu = next((line.split(":")[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name")), "n/a")
    url = make_url(db_url)
    compose = url.username == "qrypt"
    return {
        "machine": f"{platform.platform()}, {os.cpu_count()} CPUs, {cpu}",
        "python": platform.python_version(),
        "database": f"PostgreSQL {version}, "
        + ("Docker Compose service" if compose else "local server (not the Docker Compose service)")
        + f" on {url.host}:{url.port}",
        "upstream": f"committed mock Qrypt (tests/mock_qrypt.py): {int(MOCK_LATENCY_S * 1000)} ms added latency, "
        f"{LIMIT} requests per {int(WINDOW_S)} s enforced; the live Qrypt service was not used",
        "transport": "MCP over stdio to a server process; client and server share the machine",
        "budget": f"the test database's cap is raised to {TEST_CAP_KIB:,} KiB (production cap: 10,240 KiB)",
    }


class Run:
    """A throwaway migrated, seeded database plus the mock; the context removes the database."""

    def __enter__(self):
        admin = os.environ["DATABASE_URL"]
        self.name = "qrypt_load_" + secrets.token_hex(4)
        self.url = make_url(admin).set(database=self.name).render_as_string(hide_password=False)
        with psycopg.connect(
            make_url(admin).set(database="postgres").render_as_string(hide_password=False), autocommit=True
        ) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
        self.dir = tempfile.TemporaryDirectory(prefix="qrypt-load-")
        self.probe = Path(self.dir.name) / "probe.json"
        self.mock = run_mock()
        self.env = {
            **os.environ,
            "DATABASE_URL": self.url,
            "KEY_HASH_SECRET": "load-" + secrets.token_hex(16),  # throwaway secrets for this database only
            "PEPPER": "load-" + secrets.token_hex(16),
            "QRYPT_BASE_URL": self.mock.url,
            "QRYPT_API_KEY": self.mock.token,
            "APP_PROFILE": "prod",  # INFO logs, as deployed
            "LOAD_PROBE_FILE": str(self.probe),
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=self.env,
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        role_password = "load-" + secrets.token_hex(8)  # the server runs as its own role, as deployed
        with psycopg.connect(self.url) as conn:
            conn.execute("UPDATE budget SET cap_kib = %s WHERE id = 1", (TEST_CAP_KIB,))
            conn.execute(sql.SQL("ALTER ROLE mcp_server PASSWORD {}").format(sql.Literal(role_password)))
        self.env["MCP_SERVER_URL"] = (
            make_url(self.url).set(username="mcp_server", password=role_password).render_as_string(hide_password=False)
        )
        seeded = subprocess.run(
            [sys.executable, "scripts/seed.py"], env=self.env, cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout
        self.key = dict(re.findall(r"credential role=\w+ name=(\S+) key=(\S+)", seeded))["billing-agent"]
        return self

    def params(self) -> StdioServerParameters:
        keep = {
            "PATH",
            "DATABASE_URL",
            "MCP_SERVER_URL",
            "KEY_HASH_SECRET",
            "PEPPER",
            "QRYPT_BASE_URL",
            "QRYPT_API_KEY",
            "APP_PROFILE",
            "LOAD_PROBE_FILE",
        }
        env = {k: v for k, v in self.env.items() if k in keep} | {"MCP_CLIENT_KEY": self.key}
        log = Path(self.dir.name) / "server.log"
        command = [sys.executable, "scripts/load_server.py"]
        return StdioServerParameters(
            command="/bin/sh", args=["-c", f'exec "$@" 2>>{log}', "sh", *command], env=env, cwd=str(ROOT)
        )

    def query(self, text: str, *args) -> list[tuple]:
        with psycopg.connect(self.url) as conn:
            return conn.execute(text, args).fetchall()

    def __exit__(self, *exc):
        self.dir.cleanup()
        admin = make_url(self.url).set(database="postgres").render_as_string(hide_password=False)
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(self.name)))


async def call(client: Client, tool: str, args: dict) -> tuple[float, str | None]:
    """(milliseconds, error code or None)."""
    started = time.perf_counter()
    result = await client.call_tool(tool, args)
    ms = (time.perf_counter() - started) * 1000
    return ms, (json.loads(result.content[0].text)["error"] if result.is_error else None)


async def warm_up(client: Client, mock: MockQrypt) -> None:
    """Wait for the first refill, so the pool is warm (PF-1 measures a warm pool)."""
    for _ in range(100):
        await call(client, *CALLS[0])
        if sum(1 for r in mock.log if r.size == str(REFILL_KIB) and r.status == 200):
            return
        await asyncio.sleep(0.1)
    raise SystemExit("the pool never filled")


def write(path: str, doc: dict) -> None:
    out = ROOT / path
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"results written to {path}")


async def latency(args) -> dict:
    with Run() as run:
        env = environment(run.url)
        async with Client(run.params()) as client:
            await warm_up(client, run.mock)
            ms, errors = [], []
            for i in range(args.calls):
                took, error = await call(client, *CALLS[i % 3])
                ms.append(took)
                if error:
                    errors.append(error)
        ms.sort()
        doc = {
            "run": "PF-1 latency, warm pool, calls one at a time over stdio",
            "calls": len(ms),
            "mix": "evenly across get_random_bytes(64), random_int(1..1000), generate_token(uuid_v4)",
            "p50_ms": percentile(ms, 0.5),
            "p95_ms": percentile(ms, 0.95),
            "p99_ms": percentile(ms, 0.99),
            "max_ms": round(ms[-1], 2),
            "errors": len(errors),
            "database_used": env["database"],
            "threshold_p95_ms": args.p95,
            "environment": env,
        }
        print(f"  p50 {doc['p50_ms']} ms, p95 {doc['p95_ms']} ms, p99 {doc['p99_ms']} ms, max {doc['max_ms']} ms")
        check(
            f"AC1 p95 under {args.p95} ms over {len(ms)} calls",
            doc["p95_ms"] < args.p95 and not errors,
            f"p95 {doc['p95_ms']} ms, {len(errors)} errors",
        )
        doc["checks"] = [{"check": c, "pass": ok, "detail": d} for c, ok, d in CHECKS]
        write(args.out, doc)
    return doc


def max_in_window(times: list[float]) -> int:
    """The largest number of requests that arrived within any sliding WINDOW_S seconds."""
    times = sorted(times)
    best, lo = 0, 0
    for hi, t in enumerate(times):
        while t - times[lo] >= WINDOW_S:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


async def concurrent(args) -> dict:
    with Run() as run:
        env = environment(run.url)
        ok, errors, ms = 0, {}, []
        injected = {"sent": 0, "answered_invalid_argument": 0}  # deliberate bad calls, not server errors
        async with Client(run.params()) as client:
            await warm_up(client, run.mock)
            started = time.perf_counter()

            def total() -> int:
                return ok + sum(errors.values()) + injected["sent"]

            async def caller(n: int) -> None:
                nonlocal ok
                i = n
                while time.perf_counter() - started < args.seconds or total() < args.min_calls:
                    i += args.callers
                    bad = i % 50 == 0
                    tool, arguments = INVALID if bad else CALLS[i % 3]
                    took, error = await call(client, tool, arguments)
                    ms.append(took)
                    if bad:
                        injected["sent"] += 1
                        injected["answered_invalid_argument"] += error == "invalid_argument"
                        error = None if error == "invalid_argument" else error or "accepted_invalid_call"
                    elif not error:
                        ok += 1
                    if error:
                        errors[error] = errors.get(error, 0) + 1

            await asyncio.gather(*(caller(n) for n in range(args.callers)))
            elapsed = time.perf_counter() - started
            await asyncio.sleep(1.2)  # lets the probe flush its last file
        warm_ok = run.query("SELECT count(*) FROM issuances")[0][0] - ok
        total_calls = ok + sum(errors.values()) + injected["sent"]
        server_errors = errors
        ms.sort()
        check(
            "AC2 zero server errors (injected invalid calls are counted separately and must all be refused)",
            not server_errors and injected["sent"] == injected["answered_invalid_argument"],
            f"{total_calls} calls in {elapsed:.1f} s with {args.callers} in flight; server errors {errors or 'none'}; "
            f"{injected['sent']} injected invalid calls, {injected['answered_invalid_argument']} refused",
        )
        check(
            "AC2 at least the asked duration and calls",
            elapsed >= args.seconds and total_calls >= args.min_calls,
            f"{elapsed:.1f} s, {total_calls} calls",
        )

        (used_kib,) = run.query("SELECT used_kib FROM budget")[0]
        refills = [r for r in run.mock.log if r.size != "1" and r.path]
        upstream = len(refills)
        meter_ratio = (used_kib - 1) / upstream
        served_kib = run.query("SELECT sum(bytes_consumed) FROM issuances")[0][0] / 1024
        check(
            "AC3 KiB SERVED per upstream request, startup check excluded, is 64 or more",
            upstream > 0
            and (meter_ratio if args.smoke else served_kib / upstream) >= 64
            and all(int(r.size) >= 64 for r in refills),
            f"{served_kib:.1f} KiB served / {upstream} requests = {served_kib / upstream:.2f}; "
            f"budget meter {used_kib - 1} KiB fetched = {meter_ratio:.2f} per request",
        )
        biggest = max_in_window([r.at for r in run.mock.log])
        rejected = sum(1 for r in run.mock.log if r.status == 429)
        check(
            f"AC4 at most {LIMIT} upstream requests in any {int(WINDOW_S)} s window",
            biggest <= LIMIT,
            f"maximum {biggest} over {len(run.mock.log)} requests ({rejected} answered 429)",
        )

        probe = json.loads(Path(run.probe).read_text())
        low, high = probe["low_mark"], probe["high_mark"]
        late = [f for f in probe["fetches"] if f["kib"] > 1 and f["level"] >= low]
        first = next((f for f in probe["fetches"] if f["kib"] > 1), None)
        check(
            "AC5 pool level never above the high mark",
            probe["max_level"] <= high and probe["samples"] > 0,
            f"maximum {probe['max_level'] / 1024:.1f} KiB (mark {high // 1024} KiB) over {probe['samples']} samples "
            f"every {probe['interval_ms']:.0f} ms",
        )
        check(
            "AC5 every refill began below the low mark",
            first is not None and not late,
            f"first refill began at {first['level'] / 1024 if first else float('nan'):.1f} KiB "
            f"(mark {low // 1024} KiB); {len(probe['fetches']) - 1} refills, {len(late)} started at or above it",
        )

        overlaps = run.query("""
            SELECT count(*) FROM (SELECT byte_offset < max(byte_offset + byte_count) OVER (
                PARTITION BY batch_id ORDER BY byte_offset ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS bad
              FROM issuance_batches) t WHERE bad""")[0][0]
        ranges = run.query("SELECT count(*) FROM issuance_batches")[0][0]
        check(
            "AC6 no byte served twice",
            overlaps == 0 and total_calls >= args.min_calls,
            f"{overlaps} overlapping ranges among {ranges} ranges from {ok + warm_ok} issuances",
        )
        rows = run.query("SELECT count(*) FROM issuances")[0][0]
        crossing = run.query("""SELECT count(*) FROM (SELECT issuance_id FROM issuance_batches GROUP BY issuance_id
                               HAVING count(DISTINCT batch_id) > 1) t""")[0][0]
        mismatched = run.query("""SELECT count(*) FROM issuances i WHERE bytes_consumed <>
                                 (SELECT sum(byte_count) FROM issuance_batches WHERE issuance_id = i.id)""")[0][0]
        check(
            "AC7 one audit row per successful call",
            rows == ok + warm_ok and mismatched == 0,
            f"{ok + warm_ok} successful calls (including {warm_ok} warm-up), {rows} issuances rows; "
            f"{crossing} calls span two batches and list both; {mismatched} rows whose ranges do not add up",
        )
        doc = {
            "run": "PF-2 sustained concurrency",
            "callers": args.callers,
            "seconds": round(elapsed, 1),
            "calls": total_calls,
            "successful_calls": ok,
            "throughput_calls_per_s": round(total_calls / elapsed, 1),
            "server_errors_by_code": errors,
            "injected_invalid_calls": injected,
            "server_error_rate": round(sum(server_errors.values()) / total_calls, 6),
            "note_errors": "about 1 call in 50 is deliberately invalid (length 0): each must answer invalid_argument "
            "and is reported under injected_invalid_calls, not as a server error",
            "latency_under_load_ms": {
                "p50": percentile(ms, 0.5),
                "p95": percentile(ms, 0.95),
                "p99": percentile(ms, 0.99),
                "max": round(ms[-1], 2),
            },
            "upstream_requests_excluding_startup_check": upstream,
            "kib_fetched_per_upstream_request": round(meter_ratio, 2),
            "kib_served_per_upstream_request": round(served_kib / upstream, 2),
            "max_upstream_requests_in_10s": biggest,
            "upstream_429s": rejected,
            "pool_max_level_kib": round(probe["max_level"] / 1024, 1),
            "pool_first_refill_level_kib": round(first["level"] / 1024, 1) if first else None,
            "overlapping_ranges": overlaps,
            "issuances_rows": rows,
            "calls_spanning_two_batches": crossing,
            "environment": env,
            "checks": [{"check": c, "pass": ok_, "detail": d} for c, ok_, d in CHECKS],
        }
        write(args.out, doc)
    return doc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["latency", "concurrent"])
    parser.add_argument("--calls", type=int, default=1000)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--callers", type=int, default=100)
    parser.add_argument("--min-calls", type=int, default=10_000)
    parser.add_argument("--smoke", action="store_true", help="short run: AC3 checks the fetched ratio, not the served")
    parser.add_argument("--p95", type=float, default=float(os.environ.get("GATE_P95_MS", 50)))
    parser.add_argument("--out")
    args = parser.parse_args()
    args.out = args.out or f"results/load_{args.mode}.json"
    asyncio.run(latency(args) if args.mode == "latency" else concurrent(args))
    failed = [c for c, ok, _ in CHECKS if not ok]
    print("LOAD FAILED: " + "; ".join(failed) if failed else "LOAD PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
