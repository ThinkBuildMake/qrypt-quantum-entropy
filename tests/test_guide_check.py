"""S44: the deployment guide check (`make guide-check`) passes on the real guide and fails on each kind of drift."""

from pathlib import Path

import pytest

from tools import guide_check

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs/DEPLOYMENT_GUIDE.md").read_text()
ENV = (ROOT / ".env.example").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()


def problems(guide=GUIDE, env=ENV, makefile=MAKEFILE):
    return guide_check.problems(guide, env, makefile)


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        ("make restore-drill", "make restore-drillx", "make restore-drillx does not exist"),
        ("| `LOG_LEVEL` |", "| `LOG_LEVELX` |", "LOG_LEVELX is in the guide's variable table"),
        ("Neon", "Other", "missing: Neon fallback"),
        ("| Date | Database |", "| When | Database |", "no 'date' column"),
        ("scripts/set_role_passwords.py", "scripts/nope.py", "scripts/nope.py does not exist"),
    ],
)
def test_drift_is_reported(old, new, expect):
    assert old in GUIDE
    assert any(expect in p for p in problems(GUIDE.replace(old, new)))


def test_variable_missing_from_the_guide_is_reported():
    assert any("NEW_SETTING is in .env.example" in p for p in problems(env=ENV + "NEW_SETTING=\n"))


def test_unknown_command_variable_is_reported():
    assert any("MADE_UP_VAR" in p for p in problems(GUIDE + "\n```\nMADE_UP_VAR=1 make db-up\n```\n"))
