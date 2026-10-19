### S25 — The Secure Token Agent turns a plain request into a real issued value, without the model ever seeing it

**Kind:** module
**Blocked by:** S01
**Review gate:** agents
**Size:** M — one loop with a placeholder scheme and the failure messages (agent 10–20 min)
**Builds:** chatTurn: the command-line chat (the user's screen); OpenRouter chat completions with tool list and placeholder tool results; Scripted model test double; No issued value is sent to the model provider (CP-5); The agent never holds or forwards a database credential, pepper, hashing secret or Qrypt token (AZ-3); Every model request is counted and the run total written to the results file (GC-3)
**Extends:** none
**Stubs:** MCP tools (skeleton stubs) and the real OpenRouter (replaced by the scripted model), removed by S32
**Requirements:** AZ-3, AZ-4, CP-5, FR-2.1, FR-2.2, GC-3
**Guards:** CP-5, AZ-3, GC-3

#### Why
Jonas asks in plain words and gets a code that a tool issued, and no value is ever sent to the language-model provider.

#### How it's checked
The agent runs the chat loop against a scripted model and the stub MCP server, capturing every outbound model request.

#### Acceptance criteria
AC1. A plain request issues a token and shows the real value
   Given a scripted model that calls generate_token with otp_6, owner user-42 and expiry_seconds 600 and replies "Your code is {{v1}}", and a stub tool returning 493021
   When the user types "issue a one-time reset code for user 42 that expires in 10 minutes"
   Then the printed reply is "Your code is 493021"
   Evidence: `make agent-scripted PROMPT='issue a one-time reset code for user 42 that expires in 10 minutes'`; the agent's log lines are saved to `logs/agent.log`
AC2. The value never goes to the model
   Given the same run with every outbound request to the model captured
   When the captured requests are searched for 493021
   Then there are 0 matches and the tool result sent to the model holds {{v1}} and the metadata
   Evidence: gate case `agent-no-value-to-model`; `make agent-capture-check` repeats it on any saved session
AC3. The agent offers the server's tools and no way to read stored tokens
   Given the stub MCP server
   When `make agent-tools` runs
   Then it lists get_random_bytes, random_int and generate_token and no tool that returns stored tokens or audit contents
   Evidence: `make agent-tools`
AC4. An issue request without an expiry gets 600 seconds
   Given a scripted model that omits the expiry
   When the user asks for a reset code for user 42
   Then the tool call received has expiry_seconds 600
   Evidence: gate case `agent-default-expiry`
AC5. Every model request is counted
   Given a three-turn conversation using 5 model requests
   When the run ends
   Then the recorded request count is 5
   Evidence: gate case `agent-request-count`
AC6. The server it launches gets only the client key
   Given the agent configuration with a database URL, pepper, hashing secret and Qrypt token set in other processes
   When the agent launches the MCP server and the child's environment is listed
   Then it holds the client key and none of the four others
   Evidence: gate case `agent-child-env`

#### Not in this story
- Checking each reply against tool returns: picked up by S27
- Plain-words failures and refusals: picked up by S26
- Running against the real model: picked up by S32
