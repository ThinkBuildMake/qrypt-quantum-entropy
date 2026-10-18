"""Import-direction check (CH-2): transport -> service -> upstream -> storage, nothing imports upward.

The `agent` package runs in its own process and may import neither a layer, storage nor config
(it has no database and no server secrets).
Exit 1 and print each violation when any exists.
"""

import ast
import sys
from pathlib import Path

PACKAGE = "qrypt_entropy"
RANK = {"storage": 0, "upstream": 1, "service": 2, "transport": 3}
COMMON = {"config", "errors", "logs", "models", "stubs"}  # importable from anywhere, import no layer


def _imported_parts(node: ast.AST) -> list[list[str]]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".") for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        base = node.module.split(".")
        return [[*base, alias.name] for alias in node.names] if base == [PACKAGE] else [base]
    return []


def find_violations(src: str | Path) -> list[str]:
    violations = []
    for path in sorted(Path(src, PACKAGE).rglob("*.py")):
        rel = path.relative_to(Path(src, PACKAGE))
        layer = rel.parts[0] if len(rel.parts) > 1 else None
        if layer is None and rel.stem in COMMON:
            layer = "common"
        if layer is None:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            for parts in _imported_parts(node):
                if parts[0] != PACKAGE or len(parts) < 2:
                    continue
                target = parts[1]
                where = f"{path.relative_to(src).as_posix()}:{node.lineno}"
                if layer == "agent":
                    bad = target in RANK or target in ("service", "config")  # the server's settings hold its secrets
                elif layer == "common":
                    bad = target in RANK or target == "agent"
                else:
                    bad = target == "agent" or (target in RANK and RANK[target] > RANK[layer])
                if bad:
                    violations.append(f"{where}: {layer} imports {target}")
    return violations


if __name__ == "__main__":
    found = find_violations(sys.argv[1] if len(sys.argv) > 1 else "src")
    print("\n".join(found) if found else "import direction ok (transport -> service -> upstream -> storage)")
    sys.exit(1 if found else 0)
