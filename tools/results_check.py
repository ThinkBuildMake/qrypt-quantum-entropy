"""`make results-check`: the results file holds every number EV-6 asks for. Exit 1 naming the missing section.

Usage: python -m tools.results_check [FILE]   (default results/RESULTS.md)
A section may say plainly that it was not run (the real-model tables before the human run); it may not be absent.
"""

import re
import sys
from pathlib import Path

NUMBER = r"\d+(?:\.\d+)?"
ROW = r"^\| (?!id )[^\n]+"  # a table row that is not the header
# section heading prefix -> [(pattern that must match inside the section, what it is)]
SECTIONS = {
    "Coverage": [(rf"{NUMBER}%", "a coverage percentage")],
    "Throughput and error rate": [
        (rf"Throughput: {NUMBER}", "throughput"),
        (rf"Error rate: .*{NUMBER}%", "error rate"),
    ],
    "Latency": [
        ("p50", "p50"),
        ("p95", "p95"),
        ("p99", "p99"),
        (rf"\| {NUMBER} \| {NUMBER} \| {NUMBER} \|", "latency numbers"),
    ],
    "KiB per upstream request": [(rf"Fetched per upstream request: {NUMBER} KiB", "KiB per upstream request")],
    "Golden set": [(r"\| id \| prompt \| expected tool \|", "the golden table"), (ROW, "a table row")],
    "Adversarial set": [(r"\| id \| attack \| prompt \|", "the adversarial table"), (ROW, "a table row")],
    "Database, model and request count": [
        (r"Docker Compose Postgres", "the Docker Compose Postgres"),
        (r"Model: \S", "the model name"),
        (r"Model limit: \S", "the model limit"),
        (rf"Model requests used: {NUMBER}", "the request count"),
    ],
}


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0].strip(): p for p in parts}


def check(text: str) -> list[str]:
    """The problems found, each naming the section."""
    found, problems = sections(text), []
    if "tests/llm_stub" in text or "NOT a language model" in text:
        problems.append("the file presents stub results: only results/eval/ (the real model) may be collected")
    for name, needs in SECTIONS.items():
        key = next((k for k in found if k.startswith(name)), None)
        if key is None:
            problems.append(f"missing section: {name}")
            continue
        problems += [
            f"section {name}: no {what}" for pattern, what in needs if not re.search(pattern, found[key], re.M)
        ]
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[0] if argv else "results/RESULTS.md")
    try:
        text = path.read_text()
    except FileNotFoundError:
        print(f"no results file at {path}: run `make results`")
        return 1
    problems = check(text)
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(f"{path}: all {len(SECTIONS)} required sections present")
    if "NOT RUN with the real model" in text:
        print("note: the golden and adversarial tables say NOT RUN: the real-model evaluation is a human step")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
