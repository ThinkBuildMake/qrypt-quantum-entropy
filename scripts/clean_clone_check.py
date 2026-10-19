"""`make clean-clone-check` (DP-1): follow the README only, in a clean clone, and print the elapsed time.

Clones the committed HEAD into a temporary directory and runs the README's `quickstart` block and then its `inspector`
block in one bash shell (`set -e`), so the exports carry over exactly as for a reader. With no Docker the clone gets a
database of its own (Makefile local mode), dropped afterwards. Targets: quickstart done in 10 minutes, Inspector
listing the three tools in 15 minutes from the start. SKIP_GATE=1 leaves `make gate` out. Exit 1 on a failed
block, a missing tool or a missed target.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.readme_check import blocks  # noqa: E402

TARGETS = {"quickstart": 600, "inspector": 900}  # seconds from the start
TOOLS = ("get_random_bytes", "random_int", "generate_token")


def script(named: dict[str, str], skip_gate: bool) -> str:
    quick = named["quickstart"]
    if skip_gate:
        quick = "\n".join(line for line in quick.splitlines() if line.strip() != "make gate")
    return (
        f"set -eo pipefail\n{quick}\necho @@quickstart\n"
        f"{named['inspector']}\necho @@inspector\nkill $(jobs -p) 2>/dev/null || true\n"
    )


def main() -> int:
    named = blocks((ROOT / "README.md").read_text())
    own = {"DATABASE_URL", "VIRTUAL_ENV", "UV"}  # the clone picks its own database and venv; `uv run` sets UV
    env = {k: v for k, v in os.environ.items() if k not in own}
    marks: dict[str, float] = {}
    output: list[str] = []
    with tempfile.TemporaryDirectory(prefix="qrypt-clone-") as tmp:
        clone = Path(tmp)  # its unique name gives the clone its own database (Makefile: qrypt_<dirname>)
        subprocess.run(["git", "clone", "--quiet", str(ROOT), str(clone)], check=True)
        head = subprocess.check_output(["git", "-C", str(clone), "rev-parse", "--short", "HEAD"], text=True).strip()
        print(f"clean clone of HEAD {head} in {clone}")
        start = time.monotonic()
        proc = subprocess.Popen(
            ["bash", "-c", script(named, env.get("SKIP_GATE") == "1")],
            cwd=clone, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        for line in proc.stdout:
            if line.startswith("@@"):
                marks[line.strip()[2:]] = time.monotonic() - start
            else:
                if "quickstart" in marks:  # only the Inspector block's own output counts as the tool list
                    output.append(line)
                print(line, end="", flush=True)
        code = proc.wait()
        db = subprocess.run(["make", "-s", "db-url"], cwd=clone, env=env, capture_output=True, text=True).stdout.strip()
        if "/qrypt_qrypt_clone_" in db:  # a per-clone local database (not the Compose one): drop it
            subprocess.run([sys.executable, "scripts/devdb.py", "drop", db], cwd=ROOT, check=False)
    listed = all(t in "".join(output) for t in TOOLS)
    ok = code == 0 and listed
    for name, limit in TARGETS.items():
        took = marks.get(name)
        met = took is not None and took <= limit
        ok &= met
        state = "not reached" if took is None else f"{took:.0f} s"
        print(f"{name}: {state} (target {limit} s) {'PASS' if met else 'FAIL'}")
    print(f"Inspector listed {', '.join(TOOLS)}: {'PASS' if listed else 'FAIL'}")
    print("CLEAN CLONE PASSED" if ok else "CLEAN CLONE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
