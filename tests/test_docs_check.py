"""S42: `make docs-check` passes on the real documents and fails on each kind of drift."""

import pytest

from tools import docs_check as dc

ROUTES, TOOLS = dc.actual_api()
FILES = dc.read_all()
API = "docs/technical/05-api-reference.md"
SECURITY = "docs/technical/06-security.md"
LIMITS = "docs/technical/08-limitations.md"


def check(**changes: str) -> list[str]:
    """Problems after replacing whole files (name -> text) in the real document set."""
    return dc.problems({**FILES, **changes}, ROUTES, TOOLS)[0]


def test_a_missing_section_is_reported():
    for title, file in dc.SECTIONS.items():
        name = f"docs/technical/{file}"
        problems = check(**{name: FILES[name].replace(f"# {title}\n", "# Something else\n")})
        assert any(title in p for p in problems), f"missing section not reported: {title}"


def test_a_missing_file_is_reported():
    files = {k: v for k, v in FILES.items() if "01-overview" not in k}
    assert any("01-overview.md" in p for p in dc.problems(files, ROUTES, TOOLS)[0])


def test_an_undocumented_route_is_reported():
    assert any(
        "DELETE /admin/nothing is documented but not registered" in p
        for p in dc.api_problems(FILES[API] + "\n### Route: DELETE /admin/nothing\n", ROUTES, TOOLS)
    )
    assert any(
        "route GET /health is registered but not documented" in p
        for p in dc.api_problems(FILES[API].replace("### Route: GET /health", "### Route: GET /healthz"), ROUTES, TOOLS)
    )


def test_an_undocumented_or_invented_tool_is_reported():
    text = FILES[API]
    assert any(
        "tool random_int is registered but not documented" in p
        for p in check(**{API: text.replace("### Tool: random_int", "### Tool: random_float")})
    )
    assert any(
        "tool random_float is documented but not registered" in p
        for p in check(**{API: text.replace("### Tool: random_int", "### Tool: random_float")})
    )


def test_a_missing_policy_phrase_is_reported():
    text = FILES[SECURITY]
    for phrase in dc.POLICY_PHRASES:
        stripped = text.replace(phrase, "x").replace(phrase.capitalize(), "x").replace(phrase.title(), "x")
        assert any(phrase in p for p in check(**{SECURITY: stripped})), f"missing policy phrase not reported: {phrase}"


def test_a_missing_not_built_item_is_reported():
    text = dc.under_heading(FILES[LIMITS], r"not built in v1")
    assert text
    for phrase in dc.NOT_BUILT:
        stripped = FILES[LIMITS].replace(text, text.replace(phrase, "x").replace(phrase.capitalize(), "x"))
        assert any(phrase in p for p in check(**{LIMITS: stripped})), f"missing not-built item not reported: {phrase}"


def test_each_required_heading_is_checked():
    for name, pattern, what in dc.HEADINGS:
        path = f"docs/technical/{name}"
        text = FILES[path]
        heading = next(h for h in dc.headings(text) if dc.re.search(pattern, h, dc.re.I))
        problems = check(**{path: text.replace(heading, "Renamed")})
        assert any(what in p for p in problems), f"missing heading not reported: {what}"


def test_a_secret_in_the_docs_is_reported():
    fake = "api_key = " + "Zq8" + "vK2mP9xLw4Rt7Yb1Nc5Hd"  # built in pieces so this file holds no secret
    assert any("secret scan" in p for p in check(**{SECURITY: FILES[SECURITY] + "\n" + fake + "\n"}))


def test_an_email_address_is_reported_unless_synthetic():
    assert any("e-mail" in p for p in check(**{SECURITY: FILES[SECURITY] + "\nwrite to jane.doe@gmail.com\n"}))
    assert not any("e-mail" in p for p in check(**{SECURITY: FILES[SECURITY] + "\nwrite to a@example.com\n"}))


@pytest.mark.parametrize(
    "source",
    [
        "flowchart LR\n  a[One] --> b[Two]",
        "flowchart TB\n  subgraph s[Group]\n    a --> b\n  end",
        "sequenceDiagram\n  participant A\n  participant B\n  A->>B: hello\n"
        "  alt ok\n    B-->>A: yes\n  else no\n    B-->>A: no\n  end",
        "erDiagram\n  a {\n    uuid id PK\n  }\n  a ||--o{ b : has",
        "---\nconfig:\n  layout: elk\n---\nflowchart LR\n  a --> b",
    ],
)
def test_valid_diagrams_parse(source):
    assert dc.diagram_problems(source) == []


@pytest.mark.parametrize(
    ("source", "expect"),
    [
        ("flowchart LR\n  a[One --> b[Two]", "unbalanced"),
        ("flowchart LR\n  subgraph s\n    a --> b", "without 'end'"),
        ("sequenceDiagram\n  participant A\n  A->>B: hi", "undeclared participant 'B'"),
        ("sequenceDiagram\n  participant A\n  alt x\n  A->>A: hi", "without 'end'"),
        ("erDiagram\n  a {\n    uuid id PK", "without '}'"),
        ("pie2\n  a", "unknown diagram type"),
        ("flowchart LR", "no body"),
    ],
)
def test_broken_diagrams_are_reported(source, expect):
    assert any(expect in p for p in dc.diagram_problems(source))


def test_a_broken_diagram_in_a_document_is_reported():
    broken = FILES["docs/technical/01-overview.md"] + "\n```mermaid\nflowchart LR\n  a[x --> b\n```\n"
    assert any("diagram" in p and "unbalanced" in p for p in check(**{"docs/technical/01-overview.md": broken}))


def test_an_unclosed_fence_is_reported():
    assert any("never closed" in p for p in check(**{SECURITY: FILES[SECURITY] + "\n```mermaid\nflowchart LR\n"}))
