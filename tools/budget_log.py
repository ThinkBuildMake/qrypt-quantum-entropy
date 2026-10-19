"""S46 AC6, AC7: the production cap and the final reading, against the budget log in docs/DEPLOYMENT_GUIDE.md.

`make budget-cap URL=https://host [SPENT_KIB=X]`  PUT /admin/budget with 10240 - X, then read /admin/usage back.
`make budget-reading URL=https://host [SPENT_KIB=X]`  GET /admin/usage, check usedKib + X <= 10240, print the log row.

X is the KiB already spent against earlier databases: SPENT_KIB, else the last "Running total" in the log table whose
database is not the production one. Needs ADMIN_KEY in the environment. Exit 1 when a check fails.
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

from tools.check_deployed import as_json, request

TOTAL_KIB = 10_240
GUIDE = Path("docs/DEPLOYMENT_GUIDE.md")
ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|([^|]*)\|\s*(\d+)\s*\|\s*(\d+)\s*\|", re.M)


def spent_from_log(text: str) -> int:
    """The running total of the last log row that is not the production database (0 when only the start row exists)."""
    rows = [m for m in ROW.finditer(text) if "production" not in m.group(2).lower()]
    return int(rows[-1].group(4)) if rows else 0


def usage(url: str, key: str, cafile: str | None = None) -> dict:
    status, _, text = request(f"{url}/admin/usage", "GET", key, cafile=cafile)
    data = as_json(text)
    if status != 200 or not isinstance(data, dict) or "budget" not in data:
        raise SystemExit(f"FAIL GET /admin/usage: HTTP {status} {text[:80]!r}")
    return data["budget"]


def cap(url: str, key: str, spent: int, cafile: str | None = None) -> int:
    want = TOTAL_KIB - spent
    status, _, text = request(f"{url}/admin/budget", "PUT", key, {"capKib": want}, cafile)
    if status != 200:
        print(f"FAIL PUT /admin/budget: HTTP {status} {text[:80]!r}")
        return 1
    budget = usage(url, key, cafile)
    ok = budget["capKib"] == want and budget["usedKib"] <= budget["capKib"]
    print(
        f"{'PASS' if ok else 'FAIL'} capKib={budget['capKib']} (wanted {TOTAL_KIB} - {spent} = {want}) "
        f"usedKib={budget['usedKib']}"
    )
    return 0 if ok else 1


def reading(url: str, key: str, spent: int, cafile: str | None = None) -> int:
    budget = usage(url, key, cafile)
    total = budget["usedKib"] + spent
    ok = total <= TOTAL_KIB
    print(f"{'PASS' if ok else 'FAIL'} usedKib={budget['usedKib']} + earlier {spent} = {total} (limit {TOTAL_KIB})")
    print("Add this row to the budget log in docs/DEPLOYMENT_GUIDE.md (section 8):")
    print(f"| {datetime.date.today()} | production (final reading) | {budget['usedKib']} | {total} | after the demo |")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["cap", "reading"])
    ap.add_argument("url")
    ap.add_argument("--spent", default=os.environ.get("SPENT_KIB") or None, help="KiB spent against earlier databases")
    ap.add_argument("--cafile", default=os.environ.get("CHECK_CAFILE") or None)
    args = ap.parse_args(argv)
    key = os.environ.get("ADMIN_KEY", "")
    if not key:
        print("FAIL set ADMIN_KEY in your shell (the admin key `make seed` printed)", file=sys.stderr)
        return 1
    try:
        spent = int(args.spent) if args.spent is not None else spent_from_log(GUIDE.read_text())
    except ValueError:
        print("FAIL SPENT_KIB must be a whole number", file=sys.stderr)
        return 1
    if not 0 <= spent <= TOTAL_KIB:
        print(f"FAIL SPENT_KIB must be from 0 to {TOTAL_KIB}", file=sys.stderr)
        return 1
    run = {"cap": cap, "reading": reading}[args.mode]
    return run(args.url.rstrip("/"), key, spent, args.cafile)


if __name__ == "__main__":
    sys.exit(main())
