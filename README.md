Quantum-entropy MCP server and token agent: three tools that serve Qrypt randomness, with a free-tier stack.

Everything below runs with no paid key: the Qrypt service is replaced by the committed mock (`tests/mock_qrypt.py`),
the model is not needed to run the server, and the only database is a local Postgres (GC-2).
Needs: Python 3.14 with [uv](https://docs.astral.sh/uv/), `make`, `openssl`, Node 22 (`npx`, for MCP Inspector) and
Postgres: Docker (Compose, Postgres 18) or a Postgres server at `localhost:5432` (see `docs/DEVIATIONS.md` 1).

## Quick start for graders (target: 10 minutes to a passing gate)

Run these in one shell, from the repository root. Install, configure, start the database, migrate, start the mock Qrypt,
seed, and run the regression gate:

```bash quickstart
make install
export KEY_HASH_SECRET=$(openssl rand -hex 32) PEPPER=$(openssl rand -hex 32)
export MOCK_QRYPT_TOKEN=$(openssl rand -hex 16) QRYPT_BASE_URL=http://127.0.0.1:8099
export QRYPT_API_KEY=$MOCK_QRYPT_TOKEN
make check-env
make db-up migrate
make mock-qrypt &
export MCP_CLIENT_KEY=$(make -s seed | sed -n 's/.*name=billing-agent key=//p')
make gate
```

- `make check-env` confirms `.env.example` lists every setting with a placeholder. Real values live in your shell (as above), a git-ignored `.env`, or Render's environment store; none is ever committed (CP-1).
- `make seed` prints the three client and admin keys **once**. The line above keeps the billing-agent key in `MCP_CLIENT_KEY`. To start over, `make db-reset`.
- `make gate` ends with `GATE PASSED`: lint, import direction, one language, `.env.example`, deployment guide, README, MCP snippet, secret scan, tests with coverage.
- Stop the mock with `kill %1`.

## Connect MCP Inspector (target: within 15 minutes of the start)

Still in the same shell. This writes the connection snippet below with your values to `logs/inspector.json` (git-ignored) and lists the tools through Inspector's command-line mode:

```bash inspector
make inspector-config
npx -y @modelcontextprotocol/inspector@2.11.0 --cli --config logs/inspector.json --server qrypt-entropy --method tools/list
```

Expected: `get_random_bytes`, `random_int` and `generate_token`. For the browser interface use the same config without `--cli`:

```bash
npx -y @modelcontextprotocol/inspector@2.11.0 --config logs/inspector.json --server qrypt-entropy
```

Call a tool from the terminal without Inspector: `make mcp-call TOOL=get_random_bytes ARGS='{"length":8,"encoding":"hex"}'`, or `make mcp-list` for the tool list.

## MCP connection snippet

Paste into any MCP-capable client (Claude Desktop, Cursor, Inspector). Replace the directory and every `REPLACE_WITH_...` value; the key is the billing-agent key `make seed` printed. `QRYPT_BASE_URL` points at the mock; for the real service delete that line and put the Qrypt token in `QRYPT_API_KEY` (`docs/DEPLOY.md`). The same text is committed as `docs/mcp-client.json`; `make snippet-check` launches a client from it and lists the three tools.

```json
{
  "mcpServers": {
    "qrypt-entropy": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/qrypt-quantum-entropy", "python", "-m", "qrypt_entropy.transport.mcp_server"],
      "env": {
        "MCP_CLIENT_KEY": "REPLACE_WITH_THE_BILLING_AGENT_KEY_PRINTED_BY_MAKE_SEED",
        "DATABASE_URL": "REPLACE_WITH_YOUR_DATABASE_URL",
        "KEY_HASH_SECRET": "REPLACE_WITH_YOUR_KEY_HASH_SECRET",
        "PEPPER": "REPLACE_WITH_YOUR_PEPPER",
        "QRYPT_API_KEY": "REPLACE_WITH_YOUR_QRYPT_TOKEN_OR_THE_MOCKS",
        "QRYPT_BASE_URL": "http://127.0.0.1:8099"
      }
    }
  }
}
```

`make db-url` prints the `DATABASE_URL` the Makefile uses. Tools: `get_random_bytes(length, encoding)`, `random_int(min, max)`, `generate_token(kind)`.

## Run the agent

The Secure Token Agent turns a plain request ("a 6-digit code for user-42") into a tool call and answers with a reference, never the value.

- Free and scripted, no key: `make demo-chat` resets the local database, starts the real MCP server and the mock Qrypt, and drives the agent with a scripted model.
- Interactive with a real model: set a free OpenRouter key (`export OPENROUTER_API_KEY=...`) with the variables from the quick start, then `make chat` (`docs/DEPLOY.md`).
- Learn more: `docs/technical/` (overview, modules, data flows, database, API reference, security, decisions, limitations) and `docs/DEPLOYMENT_GUIDE.md` (hosting on Render).

## Timed walk-through

`make clean-clone-check` follows this README and nothing else: it clones the committed `HEAD` into a temporary directory, runs the `quickstart` block, then the `inspector` block, each in a fresh shell on its own throwaway database, and prints the elapsed time against its target.

| Step | Commands | Target |
|---|---|---|
| Install, configure, database, migrate, seed, gate | the `quickstart` block | 10 min |
| Inspector lists the three tools | `quickstart` through `inspector` | 15 min |

Add `SKIP_GATE=1` to leave `make gate` out of the timing when you only want the Inspector time.

## More

- `make demo-issue`: the whole issue flow on a fresh local database against the mock (resets the database). Also `make demo-redeem`, `make demo-operate`, `make demo-health`, `make demo-chat`.
- `make chat`: the interactive agent with a real OpenRouter model (free model, key needed; `docs/DEPLOY.md`).
- `make report`: the nine metric panels. `make backup` and `make restore-drill`: `docs/BACKUP.md`.
- Layout (nothing imports a layer above it): `src/qrypt_entropy/` has `transport/` (MCP tools, HTTP routes, report CLI), then `service/` (one file per module), then `upstream/` (entropy source), then `storage/`; `agent/` is a separate process.
- Docs: `docs/ARCHITECTURE.md` (contracts), `docs/STUBS.md`, `docs/DEVIATIONS.md`, `docs/DEPLOY.md`, `docs/DEPLOYMENT_GUIDE.md` (hosting, backup, budget log; `make guide-check`).
- AI usage: `AI_USAGE.md` (tools and models used to build, the agent's runtime model, decisions, what was verified by running, what failed; `make aiusage-check`).
