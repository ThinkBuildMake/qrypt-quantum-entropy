"""The real stdio MCP server, plus a probe for the load run (S36). Used only by scripts/load_test.py.

It runs `qrypt_entropy.transport.mcp_server.main` unchanged and adds two observers in the same process: a thread that
reads the pool level every 10 ms (SC-1) and a wrapper that notes the pool level at the start of every upstream fetch.
Only integers are recorded, never a byte of entropy (CP-2). LOAD_PROBE_FILE names the JSON file it writes.
"""

import json
import os
import sys
import threading
import time

from qrypt_entropy.config import get_settings
from qrypt_entropy.service import pool
from qrypt_entropy.transport import mcp_server
from qrypt_entropy.upstream import source

SAMPLE_S = 0.01
started = time.monotonic()
levels: list[int] = []
fetches: list[dict] = []
done = threading.Event()


def dump() -> None:
    out = {
        "interval_ms": SAMPLE_S * 1000,
        "samples": len(levels),
        "elapsed_s": round(time.monotonic() - started, 2),
        "max_level": max(levels, default=0),
        "low_mark": get_settings().pool_low_mark_bytes,
        "high_mark": get_settings().pool_high_mark_bytes,
        "fetches": fetches,
    }
    path = os.environ["LOAD_PROBE_FILE"]
    with open(path + ".tmp", "w") as f:
        json.dump(out, f)
    os.replace(path + ".tmp", path)


def sample() -> None:
    next_at, flushed = time.monotonic(), time.monotonic()
    while not done.is_set():
        p = pool._pool
        levels.append(p.level if p else 0)
        next_at += SAMPLE_S
        time.sleep(max(0.0, next_at - time.monotonic()))
        if time.monotonic() - flushed > 1:
            dump()
            flushed = time.monotonic()


_fetch = source.fetch


async def noting_fetch(size_kib: int):
    p = pool._pool
    fetches.append({"at_s": round(time.monotonic() - started, 3), "kib": size_kib, "level": p.level if p else 0})
    return await _fetch(size_kib)


if __name__ == "__main__":
    source.fetch = noting_fetch
    threading.Thread(target=sample, daemon=True).start()
    try:
        code = mcp_server.main()
    finally:
        done.set()
        dump()
    sys.exit(code)
