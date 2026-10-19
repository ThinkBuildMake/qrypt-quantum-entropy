# AI usage

This file says which AI tools shaped this repository, for what, what the human decided, what was checked by running code and what only by other agents, and what went wrong. It is written from the repository's own history (`git log`, commit trailers, `docs/`) and the orchestration notes kept beside the work. Where the history does not record something, this file says so instead of guessing.

Two things are kept apart throughout: the tools and models that **helped write the code** (this file, first section) and the model that **runs inside the product** at run time (the Runtime model section).

## Tools and models used to build

| Tool or model | Used for | Evidence in the repository |
|---|---|---|
| Claude Code (Anthropic's command-line coding agent) with Claude Sonnet 5.5 | Everything below: the design documents, the stories, all source, tests, scripts, migrations, docs and this file | Every commit made by the agent ends with the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: <claude.ai session link>`. Author `Claude` on every build commit |
| Claude Opus, as read-only reviewer agents | Per-story review (spec review and logic review) of each diff before it was landed | Not recorded in commits (reviewers committed nothing). Recorded only in the orchestrator's notes, which are git-ignored; the fixes they caused are in `docs/STUBS.md` under the "review notes" and "review fixes" entries |
| Parallel implementer subagents, one per story, each in its own git worktree | Writing one story at a time, test first | Branch per story, squashed to one commit per story; the commit subject is `S<nn>: <story title>` |
| ruff, pytest, coverage, the committed mock Qrypt (`tests/mock_qrypt.py`), `tools/secret_scan.py` | Not AI tools: the deterministic checks the agents' work had to pass | `make gate` |

Human work in the history: the first two commits (author Michael Chuang) add the assessment brief and the PRD (`docs/Portal PRD.md`, `docs/Qrypt - Quantum Entropy MCP Server.md`). Everything after is agent-authored on the human's instructions and decisions.

What the agent did, in order, with the skill that drove it (skills are Markdown instruction files in the git-ignored .claude directory, so they are not in the repository):

1. Users and problem: skill define-users produced `docs/USERS.md` and `docs/USERS_handoff.md`.
2. Requirements: skill requirements-doc produced `docs/REQUIREMENTS.md`; skill define-key-metrics produced `docs/KEY_METRICS.md`.
3. Design: skills high-level-design, choose-stack and architecture-design produced `docs/HIGH_LEVEL_DESIGN.md`, `docs/STACK.md` and `docs/ARCHITECTURE.md`. A separate agent that had not seen the research checked the chosen stack, and a fresh review was applied to the architecture (commit "Apply fresh-review findings to ARCHITECTURE.md").
4. Stories: skill story-writer produced the 46 stories in `docs/stories/` (commit "Add stories").
5. Build: skill lazy-orchestrator ran the loop. Per story a planner (skill story-planner) split it into subtasks; implementers followed skills coding and tdd (test first, red then green) and simplify; reviewers followed spec-review and logic-review. The story was rebased on the `build` branch, had to pass `make gate`, and was squashed into one commit.
6. Hardening: commits H01 and H02 are whole-system passes (an audit of the code against the PRD and the requirements) rather than stories.

The skills speckit-* and demo-video exist in the same skills directory; the history shows no use of them.

## Prompts and configuration

The prompts were instruction files, not chat transcripts, and the chat history is not part of the repository. The configuration that shaped the work:

- Skill files (git-ignored, listed above) gave each stage its method: how to interview, how to write a requirement with a number and a verification method, how to write an acceptance criterion with evidence, how to run test-first.
- The implementer brief and reviewer brief (also git-ignored, in the orchestrator's folder) told each agent: work only in its worktree, never push or merge, replace only the stubs the story owns, update `docs/STUBS.md`, keep `make gate` green, never log or persist random values, tokens or secrets, test against the committed mock and never live Qrypt, do not change acceptance criteria (stop and report instead). Reviewers were read-only and blocked only on a false acceptance criterion, a broken contract, or a possible leak or double count.
- The design documents and stories are themselves the prompts the builders worked from: `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/STACK.md`, `docs/stories/` and `docs/STUBS.md` (which stories left behind for the next).
- Deviations the agents had to take because the build sandbox differs from the documents are in `docs/DEVIATIONS.md` (no Docker, so a local Postgres 16 instead of Postgres 18; no Qrypt, OpenRouter, Render or GitHub account).
- No secret was given to any agent: no Qrypt token, OpenRouter key, Render or GitHub credential existed in the build sandbox. `.env.example` holds placeholders only, `make secret-scan` scans the full history (CP-1).

## Runtime model of the Secure Token Agent

The agent in `src/qrypt_entropy/agent/` calls a hosted language model through OpenRouter's OpenAI-compatible API. The default is set by `AGENT_MODEL` in `.env.example`: `google/gemma-4-31b-it:free`, a free tool-calling model. It is not Claude and not any of the building tools above; it is only used when someone runs `make chat` (or `make eval-golden`) with an `OPENROUTER_API_KEY`.

- The model never sees an issued value: the agent swaps a placeholder for the real value in its reply (decision 7 in `docs/REQUIREMENTS.md`), and a reply check strips any value the tools did not return (`src/qrypt_entropy/agent/reply_check.py`).
- The MCP server itself (the three tools) uses no language model.
- Offline, `make demo-chat` and the test suite use a scripted stand-in model, not a real one (`tests/llm_stub.py`). Results from that stub are labelled as such and never shown as model results.
- The real model has not been run in this repository's history: `results/RESULTS.md` says the golden-set section is NOT RUN with the real model.

## Decisions the human took

Recorded in the documents with who decided; the agent proposed, the owner chose:

- A role-capable SQL database instead of SQLite, so the application's credentials cannot widen their own rights (open item 2 and GC-5 in `docs/REQUIREMENTS.md`).
- Qrypt is the only entropy source and every upstream fault fails closed; no fallback to the operating system's generator (key decision 3).
- Issued values stay out of the model's context (CP-5, accepted 2026-10-17); seed tokens are flagged as seed data with no audit record (DP-6); the MCP server writes the token hash with an insert-only role.
- Benchmark against the local database; cold start accepted (PF-1, PF-5); Docker Compose chosen for the local database (commit "Apply owner decisions: cold start accepted, PF-1 reworded, Docker Compose chosen").
- The owner bought $10 of OpenRouter credit, which lifts the free-model limit to 1,000 requests a day (GC-3).
- The owner asked to prune low-value tests (static values, config defaults, document-text checks, duplicates of `make` checks): 35 tests deleted and the fault-suite and document-check lists folded into fewer parametrised cases (suite 640 collected tests down to 552).
- Deadline time zone, cut policy and spend were settled in the users documents.
- Left to the human, by design: live Qrypt use, the real-model evaluation, the Render deployment and the demo video (`docs/DEPLOY.md`, `docs/DEPLOYMENT_GUIDE.md`).

Decisions the orchestrating agent took without asking, flagged for the human in `docs/DEVIATIONS.md`: the pepper setting is `PEPPER` (the name `TOKEN_PEPPER` is an alias); a take waits 1.8 s so the error is out under 2 s (deviation 12); the startup check's bytes are served last (deviation 17); residual risks such as the owner lock-out (deviation 13) and the admin token-confirmation gap (H02-2) were written down rather than silently accepted.

## Verified by running

On the build machine (Python 3.14, local Postgres 16, committed mock Qrypt), by `make gate` and the targets it calls, which the agents ran before each story landed:

- Lint, import direction, one language, `.env.example`, deployment guide, README, MCP snippet, secret scan over the full history, results file, and the test suite with branch coverage (`make gate`).
- Measured, not asserted: latency and 100-caller load (`make load`, figures in `results/RESULTS.md`), 1,000 malformed inputs (`make fuzz`), fault injection and the two-process budget (`make fault-suite`, `make two-process-budget`), leak scan of files, logs, responses, tables, a database dump and git history (`make leak-scan`), the encrypted backup and restore drill (`make restore-drill`), and a timed clean-clone walk through the README (`make clean-clone-check`).
- Not verified by running here: anything needing Docker or Postgres 18, a live Qrypt token, a real OpenRouter model, a Render deployment (TLS, roles), a GitHub Actions run, MCP Inspector's browser interface, and the demo video. The list with reasons is in `docs/DEVIATIONS.md` (deviations 1 to 4, 7, 11, 14, 18, 21, 22).
- Even where a test passes, it passes against the mock Qrypt, which returns the same bytes in each new process (deviation 17), so it says nothing about the real service's randomness.

## Verified only by agents

- Design documents: no running code checks that they are right. They were reviewed by a fresh agent, then by the owner on the points listed as open items, and only partly.
- The spec-review and logic-review of each story, run by read-only reviewer agents, are judgement, not execution; they were backed by `make gate`, which the reviewers also ran, but a weak test the reviewer did not mutate could have been missed.
- The story-level claims in `docs/STUBS.md` and `docs/technical/` were checked for form by `make docs-check` (sections present, routes and tools match, diagrams parse); whether the prose is correct was judged by an agent.
- The whole-system audit (H01, H02) compared the code with the PRD by reading; it was an agent's reading.
- One story landed without a second review after a fix (S38, pool shutdown), and the orchestrator's notes list it for re-check.

## Failures found and fixed

Examples from the history, not a full list; each is described in `docs/STUBS.md` or `docs/DEVIATIONS.md`:

- S37 (found by the fuzz suite): an owner or `/verify` value holding a lone surrogate or a NUL was accepted, then failed late; a `generate_token` call had already drawn bytes before answering `temporarily_unavailable`. Now rejected as `invalid_argument` before any draw.
- S38 (found by the leak scan): uvicorn's own start, stop and access lines were plain text outside the JSON log format. Now routed through the JSON formatter.
- S39 (found by the fault suite): the pool recorded every batch with source `qrypt`, even from another source. It now records the active source's label.
- S27 (found by review): ids in tool metadata (batch and reference ids) were not counted as backed values, so provenance quoted in a reply was removed; values glued to punctuation or words (for example "OTP-731905") escaped the reply check. Both fixed.
- S26 (follow-up entry in `docs/STUBS.md`): the agent's reply override is now applied only when a cannot-issue error occurred and no call in the turn succeeded; the history does not say whether review or testing found the earlier behaviour.
- S25 (review notes in `docs/STUBS.md`): review led to the agent reading its own settings only, never the server's secrets, with a check that forbids importing the server configuration. The history records the design, not what the first version did.
- H01 (hardening): the hosted service was wired to the database owner's connection string instead of a restricted role, and schema routes were public. Fixed to role connection strings and schema routes off.
- H02 (audit against the PRD): the served-to-fetched ratio, tool descriptions, trace-by-token and the load report did not match the PRD. Fixed.
- Documentation drift: `docs/DEVIATIONS.md` reused deviation numbers after parallel merges; renumbered in H01. Parallel branches could also break each other's secret scan, which reads all branches (orchestrator notes).
- Known gaps not fixed, recorded instead: the owner lock-out (deviation 13), the admin token confirmation (H02-2), a weak upstream-limit test (only five refills against the mock).
