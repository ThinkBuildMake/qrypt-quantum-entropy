# Technical documentation

How the Qrypt Quantum Entropy MCP Server and Secure Token Agent is built, what it guarantees and what it does not. It
describes the code on the `build` branch as read from the code, not the plan. `make docs-check` keeps it honest: it
fails when a section is missing, when the API reference and the registered routes and tools differ, when a diagram does
not parse, or when a secret or a non-synthetic e-mail address is in `docs/`.

**Reading order.** For a quick view read 01 and 08. For a security review read 06 and 08. For an API consumer, 05.

| # | Document | Question it answers |
|---|---|---|
| 01 | [Overview and layering](01-overview.md) | What runs where, and what may call what? |
| 02 | [Modules](02-modules.md) | What does each module own and expose? |
| 03 | [Data flows](03-data-flows.md) | What happens, step by step, on each operation? |
| 04 | [Database schema](04-database.md) | Which tables, which roles, which guards? |
| 05 | [API reference](05-api-reference.md) | Which routes and tools exist, and what do they take and return? |
| 06 | [Security and data handling](06-security.md) | What is protected how; what reaches the model provider; what is open with Qrypt? |
| 07 | [Decisions and assumptions](07-decisions.md) | Which choices were made, which deviate from the design? |
| 08 | [Limitations](08-limitations.md) | What this does not do or prove, and what is not built? |

**Marks used.** `[unverified]` means the statement rests on a document or a belief and was not observed in a run on
this build: nothing here was run against live Qrypt, OpenRouter, Render or GitHub Actions (no credentials existed).
`[as built]` marks a place where the code differs from `ARCHITECTURE.md`. Requirement ids (CP-1, FR-6.2 and so on) refer
to `docs/REQUIREMENTS.md`, story ids (S20 and so on) to `docs/stories/`.

**Names in these documents.** Every client, owner and person name is synthetic: `admin`, `billing-agent`,
`support-agent`, `seed-owner-1` to `seed-owner-3`, `user-42`. No real customer data, key or token appears.
