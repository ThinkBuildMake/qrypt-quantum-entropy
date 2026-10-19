"""`make docs-check`: the technical documentation (docs/technical/) is complete, matches the code and holds no secret.

Checks, each reported as one line; exit 1 when any fails:
  1. every required section exists as a heading in its file (S42 AC1) and the limitation headings exist (AC5);
  2. the API reference names exactly the registered HTTP routes and MCP tools (AC2);
  3. every Mermaid diagram under docs/ parses (AC3). The parser here is a structural check written for this repository
     (header, brackets, block balance, declared participants, entity braces); it is not the Mermaid renderer;
  4. the data-policy and Qrypt-terms phrases are present (AC4);
  5. no secret and no e-mail address outside a synthetic domain is in docs/ (AC6).
"""

import asyncio
import re
import sys
from pathlib import Path

from tools.secret_scan import scan_text

DOCS = Path("docs")
TECH = DOCS / "technical"

# AC1: section title -> the file that holds it as a "## " heading.
SECTIONS = {
    "Overview and layering": "01-overview.md",
    "Modules": "02-modules.md",
    "Data flows": "03-data-flows.md",
    "Database schema": "04-database.md",
    "API reference": "05-api-reference.md",
    "Security and data handling": "06-security.md",
    "Decisions and assumptions": "07-decisions.md",
    "Limitations": "08-limitations.md",
}
# AC4 and AC5: (file, regex a heading in it must match, what it is for).
HEADINGS = [
    ("06-security.md", r"model provider.*data policy", "the model provider's data policy"),
    ("06-security.md", r"open questions for qrypt", "the open Qrypt questions"),
    ("08-limitations.md", r"reply scan.*check.*not a proof", "the reply scan is a check, not a proof"),
    ("08-limitations.md", r"trust boundary of the issuing role", "the trust boundary of the issuing role"),
    ("08-limitations.md", r"one live process per qrypt key", "one live process per Qrypt key"),
    ("08-limitations.md", r"30-day database life", "the 30-day database life"),
    ("08-limitations.md", r"not built in v1", "what is not built in v1"),
]
# AC4: each phrase must appear in the security section; AC5: each must appear under "not built in v1".
POLICY_PHRASES = ["retention", "training", "opt-out", "Qrypt Properties", "pooling", "rate ceiling"]
NOT_BUILT = ["quotas", "HTTP MCP", "dashboard", "second client"]

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
FENCE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
SYNTHETIC_DOMAINS = {"example.com", "example.org", "example.net", "anthropic.com"}  # anthropic.com: commit trailer only
ROUTE_HEADING = re.compile(r"^### Route: (GET|POST|PUT|PATCH|DELETE) (/\S*)\s*$", re.M)
TOOL_HEADING = re.compile(r"^### Tool: (\w+)\s*$", re.M)


def headings(text: str) -> list[str]:
    return [m.group(2).strip() for m in HEADING.finditer(text)]


def section_problems(files: dict[str, str]) -> list[str]:
    found = []
    for title, name in SECTIONS.items():
        text = files.get(name)
        if text is None:
            found.append(f"missing file docs/technical/{name} (section '{title}')")
        elif not any(h.lower() == title.lower() for h in headings(text)):
            found.append(f"docs/technical/{name} has no heading '{title}'")
    for name, pattern, what in HEADINGS:
        if not any(re.search(pattern, h, re.I) for h in headings(files.get(name, ""))):
            found.append(f"docs/technical/{name} has no heading for {what}")
    return found


def under_heading(text: str, pattern: str) -> str:
    """The text from the first heading matching `pattern` to the next heading of the same or a higher level."""
    marks = list(HEADING.finditer(text))
    for i, m in enumerate(marks):
        if re.search(pattern, m.group(2), re.I):
            end = next((n.start() for n in marks[i + 1 :] if len(n.group(1)) <= len(m.group(1))), len(text))
            return text[m.end() : end]
    return ""


def phrase_problems(files: dict[str, str]) -> list[str]:
    security = files.get("06-security.md", "").lower()
    found = [f"the security section never says '{p}'" for p in POLICY_PHRASES if p.lower() not in security]
    body = under_heading(files.get("08-limitations.md", ""), r"not built in v1").lower()
    found += [f"'not built in v1' never says '{p}'" for p in NOT_BUILT if p.lower() not in body]
    return found


def actual_api() -> tuple[set[str], set[str]]:
    """The routes the HTTP app registers ('GET /health') and the tools the MCP server registers."""
    from qrypt_entropy.transport import http_app, mcp_server

    routes = {f"{m} {r.path}" for r in http_app.app.routes for m in getattr(r, "methods", None) or () if m != "HEAD"}
    server = mcp_server.build_server(lambda: None, run_pool=False)
    return routes, {t.name for t in asyncio.run(server.list_tools())}


def api_problems(reference: str, routes: set[str], tools: set[str]) -> list[str]:
    documented_routes = {f"{m} {p}" for m, p in ROUTE_HEADING.findall(reference)}
    documented_tools = set(TOOL_HEADING.findall(reference))
    found = [f"route {r} is registered but not documented" for r in sorted(routes - documented_routes)]
    found += [f"route {r} is documented but not registered" for r in sorted(documented_routes - routes)]
    found += [f"tool {t} is registered but not documented" for t in sorted(tools - documented_tools)]
    found += [f"tool {t} is documented but not registered" for t in sorted(documented_tools - tools)]
    return found


# ---- Mermaid: a structural parse ------------------------------------------------------------------------------

_OPEN, _CLOSE = "([{", ")]}"
_SEQ_MESSAGE = re.compile(r"^(\S+?)\s*(--?>>|--?>|--?x|--?\)|-->)\s*[+-]?(\S+?)\s*:\s*\S.*$")
_SEQ_DECL = re.compile(r"^(?:participant|actor)\s+(\w+)(?:\s+as\s+.+)?$")
_SEQ_BLOCK_OPEN = re.compile(r"^(alt|opt|loop|par|critical|break|rect)\b")
_SEQ_BLOCK_MID = re.compile(r"^(else|and|option)\b")
_FLOW_EDGE = re.compile(r"(-->|---|-\.->|==>|-\.-|--[ox]|<-->)")
_ER_REL = re.compile(r"^\w+\s+[|}o][|o]--[|o][|{o]\s+\w+\s*:\s*\S.*$")


def _balanced(line: str) -> bool:
    stack = []
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch in _OPEN:
            stack.append(ch)
        elif ch in _CLOSE:
            if not stack or _OPEN.index(stack.pop()) != _CLOSE.index(ch):
                return False
    return not stack and not quoted


def diagram_problems(source: str) -> list[str]:
    """Why this Mermaid source would not parse, or [] when it is structurally sound."""
    source = re.sub(r"\A\s*---\n.*?\n---\n", "", source, flags=re.S)  # YAML front matter (config: layout: elk)
    lines = [ln.strip() for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("%%")]
    if not lines:
        return ["empty diagram"]
    head, body = lines[0], lines[1:]
    kind = head.split()[0]
    if kind in ("flowchart", "graph"):
        return _flowchart_problems(body)
    if kind == "sequenceDiagram":
        return _sequence_problems(body)
    if kind == "erDiagram":
        return _er_problems(body)
    if kind in ("stateDiagram-v2", "stateDiagram", "gantt", "classDiagram", "journey", "pie"):
        return [] if body else ["no body"]
    return [f"unknown diagram type '{kind}'"]


def _flowchart_problems(body: list[str]) -> list[str]:
    found, depth = [], 0
    if not body:
        found.append("no body")
    for line in body:
        if line.startswith("subgraph"):
            depth += 1
        elif line == "end":
            depth -= 1
            if depth < 0:
                found.append("'end' without 'subgraph'")
                depth = 0
        elif not _balanced(line):
            found.append(f"unbalanced brackets or quotes: {line[:60]}")
        elif not (_FLOW_EDGE.search(line) or re.match(r"^(direction|style|classDef|class|linkStyle|click)\b", line)):
            if not re.match(r"^\w+\s*(\[|\(|\{|>)", line):
                found.append(f"not a node, edge or directive: {line[:60]}")
    if depth:
        found.append("'subgraph' without 'end'")
    return found


def _sequence_problems(body: list[str]) -> list[str]:
    found, declared, depth = [], set(), 0
    for line in body:
        if m := _SEQ_DECL.match(line):
            declared.add(m.group(1))
        elif _SEQ_BLOCK_OPEN.match(line):
            depth += 1
        elif line == "end":
            depth -= 1
            if depth < 0:
                found.append("'end' without a block")
                depth = 0
        elif _SEQ_BLOCK_MID.match(line) or re.match(r"^(Note|autonumber|activate|deactivate|title)\b", line):
            continue
        elif m := _SEQ_MESSAGE.match(line):
            found += [f"undeclared participant '{p}'" for p in (m.group(1), m.group(3)) if p not in declared]
        else:
            found.append(f"not a message or declaration: {line[:60]}")
    if depth:
        found.append("block without 'end'")
    return found


def _er_problems(body: list[str]) -> list[str]:
    found, inside = [], False
    for line in body:
        if inside:
            inside = line != "}"
        elif re.match(r"^\w+\s*\{$", line):
            inside = True
        elif re.match(r"^\w+$", line):  # an entity named without attributes
            continue
        elif not _ER_REL.match(line):
            found.append(f"not an entity or relationship: {line[:60]}")
    if inside:
        found.append("entity block without '}'")
    return found


def diagram_files(root: Path = DOCS) -> list[Path]:
    return sorted(root.rglob("*.md"))


def mermaid_problems(files: dict[str, str]) -> tuple[int, list[str]]:
    count, found = 0, []
    for name, text in files.items():
        if text.count("```") % 2:
            found.append(f"{name}: a code fence is never closed")
        for n, block in enumerate(FENCE.findall(text), 1):
            count += 1
            found += [f"{name}: diagram {n}: {p}" for p in diagram_problems(block)]
    return count, found


def leak_problems(files: dict[str, str]) -> list[str]:
    found = []
    for name, text in files.items():
        found += [f"secret scan: {f}" for f in scan_text(text, name)]
        found += [
            f"{name}: e-mail address at a non-synthetic domain: {m.group(0)}"
            for m in EMAIL.finditer(text)
            if m.group(1).lower() not in SYNTHETIC_DOMAINS
        ]
    return found


def read_all() -> dict[str, str]:
    return {str(p): p.read_text() for p in diagram_files()}


def problems(files: dict[str, str], routes: set[str], tools: set[str]) -> tuple[list[str], int]:
    """`files` maps a path under docs/ to its text. Returns (problems, number of diagrams parsed)."""
    tech = {Path(p).name: t for p, t in files.items() if Path(p).parent == TECH}
    diagrams, mermaid = mermaid_problems(files)
    found = section_problems(tech) + phrase_problems(tech)
    found += api_problems(tech.get("05-api-reference.md", ""), routes, tools)
    return found + mermaid + leak_problems(files), diagrams


if __name__ == "__main__":
    texts = read_all()
    real_routes, real_tools = actual_api()
    issues, parsed = problems(texts, real_routes, real_tools)
    if issues:
        print("\n".join(issues))
    else:
        print(
            f"docs/technical is complete: {len(SECTIONS)} sections, {len(real_routes)} routes and "
            f"{len(real_tools)} tools match the code, {parsed} diagrams parse, 0 secrets"
        )
    sys.exit(1 if issues else 0)
