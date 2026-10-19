"""S45: the AI usage check passes on the real file and fails on each kind of drift."""

from pathlib import Path

from tools import aiusage_check as ac

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "AI_USAGE.md").read_text()
ENV = (ROOT / ".env.example").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()


def problems(text=TEXT):
    return ac.problems(text, ENV, MAKEFILE, exists=lambda p: (ROOT / p).exists())


def test_a_missing_section_is_reported():
    for title in ac.SECTIONS:
        assert any(title in p for p in problems(TEXT.replace(f"## {title}", "## Other"))), (
            f"missing section not reported: {title}"
        )


def test_a_missing_file_is_reported():
    assert any("docs/no/such.py" in p for p in problems(TEXT + "\n`docs/no/such.py`\n"))


def test_a_missing_make_target_is_reported():
    assert any("nope" in p for p in problems(TEXT + "\nRun make nope.\n"))


def test_runtime_model_in_building_section_is_reported():
    model = next(line.split("=", 1)[1].split()[0] for line in ENV.splitlines() if line.startswith("AGENT_MODEL="))
    bad = TEXT.replace("## Prompts and configuration", f"\nAlso {model}.\n\n## Prompts and configuration", 1)
    assert any("keep them apart" in p for p in problems(bad))


def test_a_secret_is_reported():
    assert any("secret scan" in p for p in problems(TEXT + "\nAK" + "IAIOSFODNN7EXAMPLQ\n"))
