"""`make guide-check`: the deployment guide has every required step and names only what exists. Exit 1 on problems."""

import re
import sys
from pathlib import Path

GUIDE = Path("docs/DEPLOYMENT_GUIDE.md")
ENV_NAME = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=", re.M)
MAKE_TARGET = re.compile(r"\bmake ([a-z][a-z0-9-]*)")
SCRIPT_PATH = re.compile(r"\b((?:scripts|tools|tests)/[\w./-]+\.py)\b")
SECTION = re.compile(r"^#{2,3} (.+)$", re.M)
TABLE_NAME = re.compile(r"^\| `([A-Z][A-Z0-9_]+)` \|", re.M)
SHELL_USE = re.compile(r"(?:\$\{?|^|\s)([A-Z][A-Z0-9_]{3,})(?:=|\b)", re.M)

# AC1, AC4, AC5: each phrase the guide must contain, matched case-insensitively.
REQUIRED = {
    "Render web service step": r"render web service",
    "Render Postgres step": r"render postgres",
    "roles spike step": r"make spike-roles",
    "Neon fallback": r"neon",
    "production database on or after 2026-10-20, spike database deleted first": r"2026-10-20.*delet|delet.*2026-10-20",
    "key pair": r"age-keygen",
    "private key kept offline": r"offline",
    "secret BACKUP_URL": r"BACKUP_URL",
    "secret BACKUP_AGE_RECIPIENT": r"BACKUP_AGE_RECIPIENT",
    "daily schedule": r"03:17 UTC",
    "restore drill": r"make restore-drill",
    "30-day database life": r"30 days",
    "no platform backups": r"no (platform )?backups",
    "sleep after 15 minutes idle": r"15 minutes",
    "about 1 minute to wake": r"about 1 minute",
    "OpenRouter limits": r"20 requests a minute.*50 a day|50 a day.*20 requests a minute",
    "OpenRouter limit with credit": r"1,000 (requests )?a day",
    "own meter per database": r"own meter",
    "earlier database KiB still count": r"still count",
    "10,240 KiB cap": r"10,240 KiB",
}


def make_targets(makefile: str) -> set[str]:
    return set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.M))


def section(text: str, title: str) -> str:
    parts = SECTION.split(text)
    return next((parts[i + 1] for i in range(1, len(parts) - 1, 2) if parts[i].lower().startswith(title)), "")


def budget_log_header(text: str) -> list[str]:
    for line in text.splitlines():
        cells = [c.strip().lower() for c in line.strip("|").split("|")]
        if line.startswith("|") and "date" in cells:
            return cells
    return []


def problems(guide: str, env_example: str, makefile: str, exists=lambda p: Path(p).exists()) -> list[str]:
    found = [f"missing: {what}" for what, pattern in REQUIRED.items() if not re.search(pattern, guide, re.I | re.S)]
    example = set(ENV_NAME.findall(env_example))
    documented = set(TABLE_NAME.findall(section(guide, "environment variables")))
    found += [f"{n} is in .env.example but not in the guide's variable table" for n in sorted(example - documented)]
    found += [f"{n} is in the guide's variable table but not in .env.example" for n in sorted(documented - example)]
    shell = set(TABLE_NAME.findall(section(guide, "variables used only in commands")))
    for block in re.findall(r"```.*?```", guide, re.S):
        for name in set(SHELL_USE.findall(block)) - example - shell:
            if name not in {
                "DROP",
                "SELECT",
                "INSERT",
                "ROLE",
                "LOGIN",
                "GRANT",
                "REVOKE",
                "CREATE",
                "FROM",
                "ORDER",
                "POST",
                "GET",
                "PUT",
            }:
                found.append(f"{name} is used in a command but is in neither variable table")
    targets = make_targets(makefile)
    found += [f"make {t} does not exist" for t in sorted(set(MAKE_TARGET.findall(guide)) - targets)]
    found += [f"{p} does not exist" for p in sorted(set(SCRIPT_PATH.findall(guide))) if not exists(p)]
    header = budget_log_header(guide)
    for need in ("date", "database", "kib spent", "running total"):
        if need not in header:
            found.append(f"budget log table has no '{need}' column")
    return found


if __name__ == "__main__":
    issues = problems(GUIDE.read_text(), Path(".env.example").read_text(), Path("Makefile").read_text())
    print("\n".join(issues) if issues else f"{GUIDE} is complete and names only what exists")
    sys.exit(1 if issues else 0)
