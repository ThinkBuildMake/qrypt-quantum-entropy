"""`make readme-check`: the README's one-line description is short, its blocks name only commands that exist, and its
snippet is the committed one. Exit 1 listing problems. Shared with `make clean-clone-check`, which runs the blocks."""

import json
import re
import sys
from pathlib import Path

from tools.guide_check import SCRIPT_PATH, SHELL_USE, make_targets

README = Path("README.md")
MAX_FIRST_LINE = 119  # `head -1 README.md | wc -m` counts the newline: 120 characters at most (DP-7)
MAKE_TARGET = re.compile(r"\bmake (?:-\w+ )*([a-z][a-z0-9-]*)")  # also `make -s seed`
BLOCK = re.compile(r"^```(\w+) (\w+)\n(.*?)^```", re.M | re.S)  # a named, runnable block: ```bash quickstart
PATH_WORD = re.compile(r"`((?:docs|scripts|tools|tests|src)/[\w./-]+)`")
INSPECTOR = "@modelcontextprotocol/inspector@"
TOOLS = ("get_random_bytes", "random_int", "generate_token")
# not settings: make arguments, the mock's own token and shell basics
SHELL_WORDS = {"PATH", "HOME", "TOOL", "ARGS", "SKIP_GATE", "MOCK_QRYPT_TOKEN"}
ORDER = {  # AC1: the quickstart does these in this order
    "quickstart": ["make install", "make check-env", "make db-up", "migrate", "make mock-qrypt", "seed", "make gate"],
    "inspector": ["make inspector-config", INSPECTOR],
}


def blocks(text: str) -> dict[str, str]:
    return {name: body for _, name, body in BLOCK.findall(text)}


def problems(
    readme: str, env_example: str, makefile: str, mcp_client: str, exists=lambda p: Path(p).exists()
) -> list[str]:
    found = []
    first = readme.split("\n", 1)[0]
    if len(first) > MAX_FIRST_LINE:
        found.append(f"first line is {len(first) + 1} characters with its newline; the limit is {MAX_FIRST_LINE + 1}")
    named = blocks(readme)
    for name, steps in ORDER.items():
        body = named.get(name, "")
        at = -1
        for step in steps:
            nxt = body.find(step, at + 1)
            found += [f"{name} block lacks `{step}` after the previous step"] if nxt < 0 else []
            at = max(at, nxt)
    names = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", env_example, re.M))
    fenced = re.findall(r"```.*?```", readme, re.S)
    for body in (b for b in fenced if "mcpServers" not in b):
        used = set(SHELL_USE.findall(body)) - names - SHELL_WORDS
        found += [f"{n} is used in a command but is not in .env.example" for n in sorted(used)]
    targets = make_targets(makefile)
    found += [f"make {t} does not exist" for t in sorted(set(MAKE_TARGET.findall(readme)) - targets)]
    mentioned = set(SCRIPT_PATH.findall(readme)) | set(PATH_WORD.findall(readme))
    found += [f"{p} does not exist" for p in sorted(mentioned) if not exists(p)]
    found += [f"tool {t} is not named" for t in TOOLS if t not in readme]
    found += [f"npx package is not pinned: {m}" for m in re.findall(r"@modelcontextprotocol/inspector(?!@\d)", readme)]
    snippets = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", readme, re.S) if "mcpServers" in b]
    if len(snippets) != 1:
        found.append(f"the README must hold exactly one mcpServers snippet, found {len(snippets)}")
    elif snippets[0] != json.loads(mcp_client):
        found.append("the README snippet differs from docs/mcp-client.json")
    return found


if __name__ == "__main__":
    issues = problems(
        README.read_text(),
        Path(".env.example").read_text(),
        Path("Makefile").read_text(),
        Path("docs/mcp-client.json").read_text(),
    )
    print("\n".join(issues) if issues else f"{README} names only commands, scripts and variables that exist")
    sys.exit(1 if issues else 0)
