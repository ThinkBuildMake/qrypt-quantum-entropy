"""`make aiusage-check`: AI_USAGE.md names the tools and models, their purposes and the configuration, keeps the
building tools apart from the agent's runtime model, names only files and make targets that exist, and holds no secret.
Exit 1 listing problems."""

import re
import sys
from pathlib import Path

from tools.guide_check import make_targets
from tools.secret_scan import scan_text

DOC = Path("AI_USAGE.md")
SECTIONS = (  # AC1, AC3: each must be a "## " heading
    "Tools and models used to build",
    "Prompts and configuration",
    "Runtime model of the Secure Token Agent",
    "Decisions the human took",
    "Verified by running",
    "Verified only by agents",
    "Failures found and fixed",
)
PATH = re.compile(
    r"`((?:docs|scripts|tools|tests|src|migrations|results|evals|\.github)/[\w./-]+|[\w.-]+\.(?:md|toml|yaml|yml|json)|Makefile|\.env\.example)`"
)
MAKE = re.compile(r"\bmake (?:-\w+ )*([a-z][a-z0-9-]*)")


def section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def problems(text: str, env_example: str, makefile: str, exists=lambda p: Path(p).exists()) -> list[str]:
    found = [f"missing section: ## {s}" for s in SECTIONS if not section(text, s).strip()]
    building, runtime = section(text, SECTIONS[0]), section(text, SECTIONS[2])
    if "Claude" not in building:
        found.append("the building tools section does not name the Claude model")
    model = re.search(r"^AGENT_MODEL=(\S+)", env_example, re.M)
    if not model or model.group(1) not in runtime:
        found.append("the runtime model section does not name AGENT_MODEL from .env.example")
    if model and model.group(1) in building:
        found.append("the runtime model appears in the building tools section: keep them apart")
    found += [f"{p} does not exist" for p in sorted(set(PATH.findall(text))) if not exists(p)]
    found += [f"make {t} does not exist" for t in sorted(set(MAKE.findall(text)) - make_targets(makefile))]
    found += [f"secret scan: {f}" for f in scan_text(text, str(DOC))]
    return found


if __name__ == "__main__":
    issues = problems(DOC.read_text(), Path(".env.example").read_text(), Path("Makefile").read_text())
    print("\n".join(issues) if issues else f"{DOC} is complete, names only files that exist, holds no secret")
    sys.exit(1 if issues else 0)
