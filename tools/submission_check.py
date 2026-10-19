"""S46 AC8, AC9: `make submission-check`, the last look before submitting. Run from the repository root.

PASS / FAIL / NOT CHECKED per item. The last push check needs the `gh` CLI, a login and a network; without them it says
NOT CHECKED rather than passing. Exit 0: all passed. 1: something failed. 2: none failed, something not checked.
"""

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEADLINE = datetime.datetime(2026, 10, 21, 4, 59, tzinfo=datetime.UTC)  # REQUIREMENTS GC-1: 2026-10-20 23:59 CDT
MAX_DESCRIPTION = 119  # characters before the newline: 120 with it, as `make readme-check` counts (DP-7)
REQUIRED = {
    "source": "src/qrypt_entropy",
    "README": "README.md",
    ".env.example": ".env.example",
    "mock upstream": "tests/mock_qrypt.py",
    "snippet": "docs/mcp-client.json",
    "technical docs": "docs/technical/README.md",
    "deployment guide": "docs/DEPLOYMENT_GUIDE.md",
    "results file": "results/RESULTS.md",
    "AI_USAGE.md": "AI_USAGE.md",
}


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, errors="replace")


def check_files(root: Path) -> tuple[str, str]:
    missing = [f"{n} ({p})" for n, p in REQUIRED.items() if not (root / p).exists()]
    return ("FAIL", "missing: " + ", ".join(missing)) if missing else ("PASS", f"all {len(REQUIRED)} present")


def check_description(root: Path) -> tuple[str, str]:
    readme = root / "README.md"
    if not readme.exists():
        return "FAIL", "no README.md"
    first = readme.read_text().split("\n", 1)[0]
    if not first.strip():
        return "FAIL", "the first line of README.md is empty"
    n = len(first)
    return ("PASS" if n <= MAX_DESCRIPTION else "FAIL"), f"{n} characters (limit 120 with the newline)"


def check_secrets(root: Path) -> tuple[str, str]:
    done = subprocess.run([sys.executable, "-m", "tools.secret_scan"], cwd=root, capture_output=True, text=True)
    if done.returncode == 0:
        return "PASS", "0 secrets in the tree and the full history"
    return "FAIL", "secrets found; run `make secret-scan` to list them (" + done.stdout.strip()[:80].replace(
        "\n", " "
    ) + ")"


def check_last_push(root: Path) -> tuple[str, str]:
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    if shutil.which("gh") is None:
        return "NOT CHECKED", f"gh is not installed; look at the Actions tab for {head[:8]}"
    try:
        done = subprocess.run(
            ["gh", "run", "list", "--limit", "20", "--json", "headSha,status,conclusion,workflowName,url"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return "NOT CHECKED", f"gh unavailable ({type(err).__name__}); look at the Actions tab for {head[:8]}"
    if done.returncode != 0:
        return "NOT CHECKED", "gh could not list runs (not logged in or offline); look at the Actions tab"
    try:
        runs = [r for r in json.loads(done.stdout) if r.get("headSha") == head and r.get("workflowName") != "backup"]
    except ValueError:
        return "NOT CHECKED", "gh answered with something unreadable"
    if not runs:
        return "NOT CHECKED", f"no workflow run for the last commit {head[:8]}: push it and wait for the gate"
    bad = [r for r in runs if r.get("status") != "completed" or r.get("conclusion") != "success"]
    if bad:
        return (
            "FAIL",
            f"{bad[0].get('workflowName')} is {bad[0].get('conclusion') or bad[0].get('status')}: {bad[0].get('url')}",
        )
    return "PASS", f"{len(runs)} run(s) for {head[:8]} succeeded"


def check_authors(root: Path) -> tuple[str, str]:
    out = git(root, "log", "--format=%aN <%aE>").stdout.split("\n")
    authors = sorted({a for a in out if a})
    if len(authors) == 1:
        return "PASS", authors[0]
    return "FAIL", f"{len(authors)} authors in the history, expected 1: " + "; ".join(authors)


def check_deadline(root: Path) -> tuple[str, str]:
    stamp = git(root, "log", "-1", "--format=%cI").stdout.strip()
    if not stamp:
        return "FAIL", "no commit"
    when = datetime.datetime.fromisoformat(stamp)
    ok = when <= DEADLINE
    return (
        "PASS" if ok else "FAIL"
    ), f"last commit {when.astimezone(datetime.UTC):%Y-%m-%d %H:%M} UTC, cut-off {DEADLINE:%Y-%m-%d %H:%M} UTC"


def check_clean(root: Path) -> tuple[str, str]:
    dirty = git(root, "status", "--porcelain").stdout.strip()
    return (
        ("FAIL", f"{len(dirty.splitlines())} uncommitted change(s): the last commit is what counts")
        if dirty
        else ("PASS", "clean")
    )


CHECKS = [
    ("required files", check_files),
    ("one-line description", check_description),
    ("secret scan, full history", check_secrets),
    ("last push is green", check_last_push),
    ("single author", check_authors),
    ("last commit inside the deadline", check_deadline),
    ("working tree clean", check_clean),
]


def run(root: Path) -> int:
    seen = set()
    for name, check in CHECKS:
        status, detail = check(root)
        seen.add(status)
        print(f"{status:<11} {name}: {detail}", flush=True)
    code = 1 if "FAIL" in seen else 2 if "NOT CHECKED" in seen else 0
    print(
        {0: "submission-check: PASSED", 1: "submission-check: FAILED", 2: "submission-check: NOT FULLY CHECKED"}[code]
    )
    return code


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))
