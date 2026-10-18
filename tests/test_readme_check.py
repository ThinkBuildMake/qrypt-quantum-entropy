"""S43: the README check passes on the real README and fails on each kind of drift; the snippet helpers work."""

import json
from pathlib import Path

import pytest

from scripts import snippet
from tools import readme_check

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
ENV = (ROOT / ".env.example").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()
CLIENT = (ROOT / "docs/mcp-client.json").read_text()


def problems(readme=README, client=CLIENT):
    return readme_check.problems(readme, ENV, MAKEFILE, client)


def test_real_readme_passes():
    assert problems() == []


def test_first_line_is_at_most_120_characters_with_its_newline():
    assert len(README.split("\n", 1)[0]) + 1 <= 120


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        ("make gate\n```", "make gatex\n```", "make gatex does not exist"),
        ("make -s seed", "make -s seedx", "make seedx does not exist"),
        ("make inspector-config", "make inspector-confg", "make inspector-confg does not exist"),
        ("`docs/ARCHITECTURE.md`", "`docs/NOPE.md`", "docs/NOPE.md does not exist"),
        ("export KEY_HASH_SECRET=", "export KEY_HASH_SECRETX=", "KEY_HASH_SECRETX is used in a command"),
        ("inspector@2.11.0 --cli", "inspector --cli", "npx package is not pinned"),
        ("make db-up migrate\n", "", "quickstart block lacks `make db-up`"),
        ("Quantum-entropy MCP server", "Quantum-entropy MCP server " + "x" * 120, "first line is"),
        ('"PEPPER": "REPLACE_WITH_YOUR_PEPPER"', '"PEPPER": "other"', "differs from docs/mcp-client.json"),
    ],
)
def test_drift_is_reported(old, new, expect):
    assert old in README
    assert any(expect in p for p in problems(README.replace(old, new, 1)))


def test_snippet_is_the_committed_client_config_and_fills_in_values():
    parsed = snippet.readme_snippet(README)
    assert parsed == json.loads(CLIENT)
    filled = snippet.fill(parsed, {"MCP_CLIENT_KEY": "k1", "QRYPT_BASE_URL": "http://127.0.0.1:1"}, root="/r")
    server = filled["mcpServers"]["qrypt-entropy"]
    assert server["args"][2] == "/r"
    assert server["env"]["MCP_CLIENT_KEY"] == "k1" and server["env"]["QRYPT_BASE_URL"] == "http://127.0.0.1:1"
    assert server["env"]["PEPPER"].startswith("REPLACE_WITH")
