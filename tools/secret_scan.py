"""Secret scan over the working tree and the full git history (CP-1).

`make gate` uses the gitleaks CLI when it is installed (CI installs it). Where it is not, this regex scanner
runs instead; it is coarser than gitleaks and is documented in docs/DEVIATIONS.md.
"""

import math
import re
import subprocess
import sys

RULES = {
    "aws-access-key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private-key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "github-token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "provider-key": re.compile(r"\bsk-(?:or-v1-|ant-)?[A-Za-z0-9_\-]{32,}"),
    "slack-token": re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
}
ASSIGNMENT = re.compile(
    r"""(?ix)(?:api[_-]?key|token|secret|passw(?:or)?d|pepper|credential)\w*["']?\s*[:=]\s*["']?([A-Za-z0-9+/_\-]{20,})"""
)
URL_PASSWORD = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:([^@\s/]{12,})@")
PLACEHOLDERS = (
    "stub",
    "example",
    "placeholder",
    "changeme",
    "xxxx",
    "password",
    "your",
    "secret-here",
    "env",
)


def _entropy(text: str) -> float:
    return -sum(text.count(c) / len(text) * math.log2(text.count(c) / len(text)) for c in set(text))


def _is_placeholder(value: str) -> bool:
    return any(p in value.lower() for p in PLACEHOLDERS) or _entropy(value) < 3.2


def scan_text(text: str, name: str) -> list[str]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES.items():
            if pattern.search(line):
                findings.append(f"{name}:{number}: {rule}")
        for pattern, rule in ((ASSIGNMENT, "secret-assignment"), (URL_PASSWORD, "url-password")):
            match = pattern.search(line)
            if match and not _is_placeholder(match.group(1)):
                findings.append(f"{name}:{number}: {rule}")
    return findings


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True, errors="replace").stdout


def scan_repository() -> list[str]:
    findings = []
    for path in _git("ls-files", "-co", "--exclude-standard").split("\n"):
        if path and not path.endswith(".lock"):
            try:
                findings += scan_text(open(path, errors="replace").read(), path)
            except OSError:
                continue
    if _has_commits():
        history = _git("log", "--all", "-p", "--no-color", "--format=commit %h")
        added = "\n".join(ln[1:] for ln in history.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        findings += [f"history: {f.split(': ', 1)[1]}" for f in scan_text(added, "history")]
    return sorted(set(findings))


def _has_commits() -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0


if __name__ == "__main__":
    if subprocess.run(["which", "gitleaks"], capture_output=True).returncode == 0:
        history = subprocess.run(["gitleaks", "git", "--no-banner", "--redact", "."]).returncode
        tree = subprocess.run(["gitleaks", "dir", "--no-banner", "--redact", "."]).returncode
        sys.exit(history or tree)
    found = scan_repository()
    print("\n".join(found) if found else "secret scan (regex fallback, tree + full history): clean")
    sys.exit(1 if found else 0)
