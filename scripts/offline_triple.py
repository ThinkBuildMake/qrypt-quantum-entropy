"""`make offline-triple` (EV-4): the unit suite three times with outbound network blocked and no Qrypt or model key.

Passes when all three runs pass, give identical per-test outcomes, make 0 outbound connection attempts and see no key.
The unit suite is tests/ without tests/gate (the gate cases launch servers and load runs; `make gate` covers them).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = 3


def run_once(report: Path) -> tuple[int, dict]:
    env = {k: v for k, v in os.environ.items() if k not in ("QRYPT_API_KEY", "OPENROUTER_API_KEY")}
    env["OFFLINE_REPORT"] = str(report)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "--ignore=tests/gate",
        "-p",
        "tests.offline_guard",
        "-q",
        "--no-cov",
        "-p",
        "no:cacheprovider",
    ]
    code = subprocess.run(cmd, cwd=ROOT, env=env, check=False).returncode
    return code, json.loads(report.read_text()) if report.exists() else {}


def main() -> int:
    reports = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(1, RUNS + 1):
            report = Path(tmp) / f"run{n}.json"
            code, data = run_once(report)
            print(
                f"run {n}: exit {code}, {data.get('tests')} tests, {data.get('failed')} failed, "
                f"{len(data.get('network_attempts', []))} network attempts, digest {str(data.get('digest'))[:12]}"
            )
            reports.append((code, data))
    problems = []
    if any(code or not data for code, data in reports):
        problems.append("a run failed")
    if len({d.get("digest") for _, d in reports}) != 1:
        problems.append("the three runs gave different results")
    if any(d.get("network_attempts") for _, d in reports):
        problems.append(f"network attempts: {reports[0][1].get('network_attempts')}")
    if any(d.get("keys_set") for _, d in reports):
        problems.append("a Qrypt or model key was set")
    first = reports[0][1].get("outcomes", {})
    for _, data in reports[1:]:
        for name in sorted(set(first) | set(data.get("outcomes", {}))):
            if first.get(name) != data.get("outcomes", {}).get(name):
                print(f"  differs: {name}: {first.get(name)} vs {data.get('outcomes', {}).get(name)}")
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"PASS: {RUNS} identical runs, 0 network calls, no Qrypt or model key")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
