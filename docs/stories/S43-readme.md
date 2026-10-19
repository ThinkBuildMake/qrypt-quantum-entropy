### S43 — README with a grader quick-start and a copy-paste MCP connection snippet

**Kind:** module
**Blocked by:** S32
**Review gate:** agents
**Size:** M — one README and one timed walk-through script (agent 10–20 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** CP-1, DP-1, DP-7, FR-3.1, FR-3.3, GC-2
**Guards:** GC-2, CP-1

#### Why
A reader with a clean clone installs, configures, seeds, tests and connects MCP Inspector in minutes, with no paid key.

#### How it's checked
The agent follows the README only, in a fresh container, and times it.

#### Acceptance criteria
AC1. Install, configure, seed and test in 10 minutes
   Given a clean clone in a fresh container with Docker and no paid keys
   When only the README's quick-start commands are run
   Then install, database start, migrate, seed and `make gate` pass within 10 minutes
   Evidence: `make clean-clone-check` prints the elapsed time
AC2. MCP Inspector connects within 15 minutes
   Given the same container
   When the README's Inspector steps are run
   Then the three tools are listed within 15 minutes of the start
   Evidence: `make clean-clone-check`
AC3. The connection snippet works
   Given the JSON snippet copied from the README
   When an MCP client is launched from it
   Then it connects and lists the three tools
   Evidence: `make snippet-check`
AC4. `.env.example` is complete
   Given the README's configuration step
   When `make check-env` runs
   Then every variable is listed with a placeholder
   Evidence: `make check-env`
AC5. The one-line description is 120 characters or fewer
   Given the README's first line
   When its characters are counted
   Then the count is 120 or fewer
   Evidence: `head -1 README.md | wc -m`

#### Not in this story
- Hosting the service: picked up by S44
