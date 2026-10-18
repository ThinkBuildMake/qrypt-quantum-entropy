# qrypt-quantum-entropy

Quantum Entropy MCP Server and Secure Token Agent. Status: **walking skeleton (S01)** — every tool, route and module
interface exists as a contract stub; see `docs/STUBS.md`.

```
make install        # uv, Python 3.14, locked deps
make db-up migrate  # Postgres: Docker Compose (18) if available, else a per-worktree database
PEPPER=local-dev-pepper make mcp-list  # the three tools over stdio;  docs/mcp-client.json is the Inspector/client snippet
make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'
make report         # nine empty metric panels
make gate           # lint, import direction, one language, secret scan, tests + coverage + matrix + fuzz + p95
make backup / make restore-drill  # daily encrypted pg_dump and its restore drill: docs/BACKUP.md
```

Layout (nothing imports a layer above it): `src/qrypt_entropy/` — `transport/` (MCP tools, HTTP routes, report CLI) ->
`service/` (one file per module) -> `upstream/` (entropy source) -> `storage/`; `agent/` is a separate process.
Docs: `docs/ARCHITECTURE.md` (contracts), `docs/STUBS.md`, `docs/DEVIATIONS.md`, `docs/DEPLOY.md`.
