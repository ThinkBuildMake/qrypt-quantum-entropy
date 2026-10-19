### S45 — AI usage log

**Kind:** module
**Blocked by:** S41, S42, S43, S44
**Review gate:** agents
**Size:** S — one document built from the repository's own history (agent 5–10 min)
**Builds:** 
**Extends:** none
**Stubs:** none
**Requirements:** CP-1, DP-7
**Guards:** CP-1

#### Why
Tells the reviewer which AI tools shaped the work, for what, and with which prompts and configuration.

#### How it's checked
The agent runs the completeness check and compares it with the repository.

#### Acceptance criteria
AC1. The log lists tools, purposes and configuration
   Given the repository
   When `make aiusage-check` reads AI_USAGE.md
   Then it has the tools and models used, what each was used for, and the prompts or configuration that shaped the work
   Evidence: `make aiusage-check`
AC2. Every file it names exists
   Given AI_USAGE.md
   When its file references are resolved
   Then each exists in the repository
   Evidence: `make aiusage-check`
AC3. It separates the building tools from the agent's runtime model
   Given AI_USAGE.md
   When it is read
   Then it states the model that runs the Secure Token Agent apart from the tools that helped write the code
   Evidence: `make aiusage-check`
AC4. It holds no secret
   Given AI_USAGE.md
   When the secret scan runs
   Then 0 secrets are found
   Evidence: `make gate`

#### Not in this story
- Submitting it: picked up by S46
