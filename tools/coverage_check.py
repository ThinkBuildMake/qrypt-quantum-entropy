"""`make coverage-check`: core-logic coverage from a coverage.py JSON report, against a threshold (EV-5).

Usage: python -m tools.coverage_check [--min 80] [--report results/coverage_report.json] [--out results/coverage.json]
Exit 1 when any core area (or the total over them) is below the threshold, or the report is missing.
Branch coverage is included: an area's figure is (covered lines + covered branches) / (statements + branches).
"""

import argparse
import json
import sys
from pathlib import Path

PREFIX = "src/qrypt_entropy/"
# EV-5's five areas -> the files that hold them.
AREAS = {
    "pool accounting": ["service/pool.py"],
    "unbiased sampling": ["service/issuance.py"],
    "budget enforcement": ["service/meter.py"],
    "token issue and verify": ["service/tokens.py"],
    "failure handling": ["errors.py", "upstream/source.py", "agent/guard.py"],
}


def percent(files: list[dict]) -> float:
    done = sum(f["summary"]["covered_lines"] + f["summary"].get("covered_branches", 0) for f in files)
    total = sum(f["summary"]["num_statements"] + f["summary"].get("num_branches", 0) for f in files)
    return round(100 * done / total, 1) if total else 100.0


def measure(report: dict) -> dict:
    by_name = {name.replace("\\", "/"): data for name, data in report["files"].items()}
    areas, missing = {}, []
    for area, names in AREAS.items():
        found = []
        for name in names:
            data = by_name.get(PREFIX + name)
            if data is None:
                missing.append(name)
            else:
                found.append(data)
        areas[area] = {"files": names, "percent": percent(found)}
    every = [by_name[PREFIX + n] for names in AREAS.values() for n in names if PREFIX + n in by_name]
    return {"areas": areas, "core_total_percent": percent(every), "missing_files": missing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=float, default=80.0)
    parser.add_argument("--report", default="results/coverage_report.json")
    parser.add_argument("--out", default="results/coverage.json")
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text())
    except FileNotFoundError:
        print(f"no coverage report at {args.report}: run `make coverage`")
        return 1
    result = measure(report) | {
        "threshold_percent": args.min,
        "suite_total_percent": round(report["totals"]["percent_covered"], 1),
    }
    failed = bool(result["missing_files"])
    for area, row in result["areas"].items():
        ok = row["percent"] >= args.min
        failed |= not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {area}: {row['percent']}% (need {args.min:g}%)  {', '.join(row['files'])}")
    ok = result["core_total_percent"] >= args.min
    failed |= not ok
    print(f"[{'PASS' if ok else 'FAIL'}] all core logic: {result['core_total_percent']}% (need {args.min:g}%)")
    print(f"whole package: {result['suite_total_percent']}%")
    if result["missing_files"]:
        print(f"[FAIL] no coverage data for: {', '.join(result['missing_files'])}")
    if not failed:  # a failing run must not overwrite the committed figures
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
