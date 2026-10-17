### S42 — Technical documentation for the reviewer

**Kind:** module
**Blocked by:** S32, S33, S34, S35
**Review gate:** agents
**Size:** M — one document set describing a finished system (agent 10–20 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** CP-1, CP-5, DP-7, FR-6.2, GC-7, GC-8
**Guards:** CP-1, GC-8

#### Why
Lets a reviewer understand how the system is built, what it guarantees and what it does not, without reading the code first.

#### How it's checked
The agent runs the documentation check against the finished system.

#### Acceptance criteria
AC1. The document set has every section
   Given the docs folder
   When `make docs-check` runs
   Then it finds overview and layering, modules, data flows, database schema, API reference, security and data handling, decisions and assumptions, and limitations
   Evidence: `make docs-check`
AC2. The API reference matches the real routes and tools
   Given the registered routes and tools
   When `make docs-check` compares them with the reference
   Then every route and tool is documented and none is documented that does not exist
   Evidence: `make docs-check`
AC3. The diagrams are valid
   Given every diagram in the docs
   When `make docs-check` parses them
   Then all parse
   Evidence: `make docs-check`
AC4. The model provider's data policy and the Qrypt terms are written down
   Given the documents
   When they are read
   Then they name the model and its provider's retention and training policy, the opt-out, and the open Qrypt questions on whether the bytes are Qrypt Properties, pooling and the rate ceiling
   Evidence: `make docs-check` fails if the headings or the named phrases (retention, training, opt-out, Qrypt Properties, pooling, rate ceiling) are missing
AC5. Limitations and not-in-v1 are stated
   Given the documents
   When they are read
   Then they state that the reply scan is a check and not a proof, the trust boundary of the issuing role, the one live process per Qrypt key, the 30-day database life and that quotas, HTTP MCP, a dashboard and a second client are not built
   Evidence: `make docs-check` looks for the headings
AC6. The documents hold no secret or personal data
   Given the docs folder
   When the secret scan and the name check run
   Then 0 secrets and only synthetic names are found
   Evidence: `make gate`

#### Not in this story
- Install and run steps: picked up by S43
- Hosting steps: picked up by S44
