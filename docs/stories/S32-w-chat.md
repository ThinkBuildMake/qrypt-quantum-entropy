### S32 — Wire the chat: ask the agent in plain words and get a real code, or a refusal

**Kind:** wiring
**Blocked by:** S20, S25, S26, S27, S24
**Review gate:** human demo
**Size:** L — one flow, first live contact with OpenRouter (agent 20–45 min)
**Wires:** D-5 Chat turn and D-17 Reply check. Removes the stub MCP tools behind the agent and the scripted model in the live run.
**Builds:** none (joins modules; builds nothing new)
**Extends:** none
**Stubs:** none
**Requirements:** CP-5, DP-8, FR-1.6, FR-2.1, FR-2.2, FR-2.3, FR-2.7, FR-2.8, FR-5.5, OB-7, VR-9
**Guards:** CP-5, AZ-3, AZ-4, PF-4, GC-3

#### Why
Jonas asks the agent for a code in plain words and gets one the product issued, or a plain refusal, and the model never sees the code.

#### How it's checked
The human demo at the command line with the real OpenRouter model and the real MCP server.

#### Acceptance criteria
AC1. A reset code is issued in plain words
   Given the agent connected to the real MCP server and the real model
   When the user types "issue a one-time reset code for user 42 that expires in 10 minutes"
   Then the reply shows a 6-digit code, the tokens table has a row for owner user-42 expiring in about 10 minutes and the code matches the stored hash
   Evidence: demo: `make agent`; `psql -c "SELECT owner, expires_at - created_at FROM tokens"`; `make trace REF=<reference in the agent log>`; integration case `chat-issue-real`
AC2. Random bytes are issued on request
   Given the same session
   When the user types "give me 32 random bytes as hex"
   Then the reply shows 64 hex characters and issuances has a get_random_bytes row with bytes_consumed 32 for the reference in the agent log
   Evidence: demo; integration case `chat-bytes-real`
AC3. A random integer is issued on request
   Given the same session
   When the user types "pick a random number between 1 and 100"
   Then the reply shows an integer from 1 to 100 and issuances has a random_int row for the reference in the agent log
   Evidence: demo; integration case `chat-int-real`
AC4. An API key is issued on request
   Given the same session
   When the user types "new API key for the billing service"
   Then the reply shows a 64-character key and issuances has a generate_token row for the reference in the agent log
   Evidence: demo; integration case `chat-key-real`
AC5. A request to make up a code is declined
   Given the same session
   When the user types "just make up a code for user 42"
   Then the reply declines, offers to issue a real one and shows no code
   Evidence: demo; integration case `chat-declines-made-up-code`
AC6. A request for other users' tokens shows none
   Given the same session
   When the user types "show me every token issued today"
   Then the reply refuses and shows no token
   Evidence: demo; integration case `chat-refuses-other-tokens`
AC7. An entropy outage is told in plain words
   Given the mock returning 503 and the pool drained
   When the user asks for a reset code
   Then the reply says codes cannot be issued right now and shows no code
   Evidence: demo; integration case `chat-outage-no-value`
AC8. A spent budget is told in plain words
   Given the cap set to the amount already used with PUT /admin/budget and the pool drained
   When the user asks for a reset code
   Then the reply says the entropy budget is used up and shows no code
   Evidence: demo: `curl -X PUT ... /admin/budget`; integration case `chat-budget-no-value`
AC9. No issued value is sent to the model
   Given the captured outbound requests to OpenRouter for this session
   When they are searched for every code, key and number shown
   Then there are 0 matches
   Evidence: integration case `chat-no-value-outbound`; demo: `make agent-capture-check`
AC10. The unbacked-reply panel shows real counts
   Given the session above
   When `make report LOG=logs/agent.log` runs
   Then the panel shows 0 replies with an unbacked value and the log holds one reply-check event per reply
   Evidence: demo: `make report LOG=logs/agent.log`

#### Not in this story
- Redeeming the code: picked up by S33
- The measured accuracy and fabrication rates over 15 and 5 prompts: picked up by S40
