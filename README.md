# qrypt-quantum-entropy

Quantum Entropy MCP Server and Secure Token Agent. Status: **the issue flow is wired (S20)** — `get_random_bytes`, `random_int` and `generate_token` run on the real pool,
meter, Qrypt source and audit; the remaining stubs are listed in `docs/STUBS.md`.

```
make install        # uv, Python 3.14, locked deps
make db-up migrate  # Postgres: Docker Compose (18) if available, else a per-worktree database
make demo-issue     # the whole issue flow on a fresh local database against the mock Qrypt (resets the database)
make mock-qrypt     # the mock Qrypt on :8099 (point QRYPT_BASE_URL at http://127.0.0.1:8099; docs/DEPLOY.md)
CLIENT_KEY=<key from make seed> make mcp-list   # the three tools over stdio;  docs/mcp-client.json is the Inspector snippet
make report         # nine empty metric panels
make gate           # lint, import direction, one language, secret scan, tests + coverage + matrix + fuzz + p95
make backup / make restore-drill  # daily encrypted pg_dump and its restore drill: docs/BACKUP.md
```

Layout (nothing imports a layer above it): `src/qrypt_entropy/` — `transport/` (MCP tools, HTTP routes, report CLI) ->
`service/` (one file per module) -> `upstream/` (entropy source) -> `storage/`; `agent/` is a separate process.
Docs: `docs/ARCHITECTURE.md` (contracts), `docs/STUBS.md`, `docs/DEVIATIONS.md`, `docs/DEPLOY.md`.
