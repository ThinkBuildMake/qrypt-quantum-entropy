"""The README's MCP connection snippet (FR-3.3): fill it in for Inspector, or launch a client from it.

python scripts/snippet.py fill PATH   write the snippet with the values in the environment (make inspector-config)
python scripts/snippet.py check       build a throwaway database and the mock, launch a client from the filled snippet,
                                      and require the three tools (make snippet-check)
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

README = ROOT / "README.md"
PLACEHOLDER_ROOT = "/ABSOLUTE/PATH/TO/qrypt-quantum-entropy"
TOOLS = {"get_random_bytes", "random_int", "generate_token"}
SERVER = "qrypt-entropy"
FILLED_FROM = ("MCP_CLIENT_KEY", "DATABASE_URL", "KEY_HASH_SECRET", "PEPPER", "QRYPT_API_KEY", "QRYPT_BASE_URL")


def readme_snippet(text: str) -> dict:
    """The first fenced json block of the README that holds `mcpServers`."""
    blocks = re.findall(r"```json\n(.*?)```", text, re.S)
    return json.loads(next(b for b in blocks if "mcpServers" in b))


def fill(snippet: dict, values: dict[str, str], root: str = str(ROOT)) -> dict:
    """The snippet with the directory and each environment value replaced (a value left out keeps its text)."""
    server = json.loads(json.dumps(snippet).replace(PLACEHOLDER_ROOT, root))["mcpServers"][SERVER]
    server["env"] = {k: values.get(k, v) for k, v in server["env"].items()}
    return {"mcpServers": {SERVER: server}}


def write_filled(path: Path) -> None:
    missing = [n for n in FILLED_FROM if n not in os.environ]
    if missing:
        raise SystemExit(f"set {', '.join(missing)} first (README: configure, seed)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fill(readme_snippet(README.read_text()), dict(os.environ)), indent=2))
    path.chmod(0o600)
    print(f"wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


async def launch_and_list(config: dict) -> set[str]:
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    server = config["mcpServers"][SERVER]
    async with Client(StdioServerParameters(command=server["command"], args=server["args"], env=server["env"])) as c:
        return {t.name for t in (await c.list_tools()).tools}


def check() -> int:
    from load_test import Run  # throwaway migrated, seeded database and the mock Qrypt

    with Run() as run:
        values = {
            "MCP_CLIENT_KEY": run.key,
            "DATABASE_URL": run.url,
            "KEY_HASH_SECRET": run.env["KEY_HASH_SECRET"],
            "PEPPER": run.env["PEPPER"],
            "QRYPT_API_KEY": run.mock.token,
            "QRYPT_BASE_URL": run.mock.url,
        }
        with tempfile.TemporaryFile("w+") as server_log:  # the server's stderr: shown only if the check fails
            saved = os.dup(2)
            os.dup2(server_log.fileno(), 2)
            try:
                tools = asyncio.run(launch_and_list(fill(readme_snippet(README.read_text()), values)))
            finally:
                os.dup2(saved, 2)
            if tools != TOOLS:
                server_log.seek(0)
                print(server_log.read()[-3000:])
    print(f"snippet launched `uv run ...` and listed: {', '.join(sorted(tools))}")
    ok = tools == TOOLS
    print("SNIPPET PASSED" if ok else f"SNIPPET FAILED: expected {sorted(TOOLS)}")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["fill"] and len(sys.argv) == 3:
        write_filled(Path(sys.argv[2]).resolve())
    elif sys.argv[1:] == ["check"]:
        sys.exit(check())
    else:
        raise SystemExit(__doc__)
