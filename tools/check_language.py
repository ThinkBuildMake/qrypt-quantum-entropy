"""One source language (GC-5): the repository's source is Python only. Exit 1 listing any other source file."""

import subprocess
import sys
from pathlib import PurePath

FOREIGN = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".scala", ".sh",
}  # fmt: skip


def find_foreign_sources(paths: list[str]) -> list[str]:
    return [p for p in paths if PurePath(p).suffix in FOREIGN]


if __name__ == "__main__":
    tracked = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard"], text=True).split("\n")
    found = find_foreign_sources([p for p in tracked if p])
    print("\n".join(f"foreign source file: {p}" for p in found) if found else "one source language: Python")
    sys.exit(1 if found else 0)
