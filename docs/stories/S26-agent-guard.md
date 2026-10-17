### S26 — The agent says plainly when it cannot issue a value and refuses unsafe requests

**Kind:** module
**Blocked by:** S25
**Review gate:** agents
**Size:** S — five small behaviours on the finished loop (agent 5–10 min)
**Builds:** When no value can be issued the agent says so and produces none itself (FR-2.8); A request to make up a code, show others' tokens or bypass budget, role or secrets is refused without a stored-token tool call (FR-2.3, FR-2.7, EV-3); Tool error: shown in plain words, no value, log event; Model unavailable, over its limit or slow: plain message, log event
**Extends:** the chat loop (S25)
**Stubs:** MCP tools (skeleton stubs) and the real model (replaced by the scripted model), removed by S32
**Requirements:** AZ-4, EV-3, FR-2.3, FR-2.7, FR-2.8, FR-5.5
**Guards:** AZ-4, CP-5

#### Why
When entropy is unavailable, the budget is spent, the model fails, or a user asks for something unsafe, the user gets a plain answer and never a value the agent made up.

#### How it's checked
The agent runs scripted models and stub tool errors and reads the replies and the captured model requests.

#### Acceptance criteria
AC1. A tool error is told in plain words with no value
   Given the stub tool returning budget_exhausted
   When the user asks for a code
   Then the reply says the entropy budget is used up and holds no code
   Evidence: gate case `agent-budget-exhausted`
AC2. An unavailable entropy source is told in plain words
   Given the stub tool returning temporarily_unavailable
   When the user asks for a code
   Then the reply says codes cannot be issued right now and holds no code
   Evidence: gate case `agent-unavailable`
AC3. A model failure gives a plain message
   Given the scripted model answering 429 and then timing out
   When the user types a request
   Then the agent prints a plain message each time, exits cleanly and logs an event
   Evidence: gate case `agent-model-down`
AC4. A tool the server did not offer is never called
   Given a scripted model that asks for a tool named list_tokens
   When the agent receives the call
   Then no tool call is made, the model is told the tool does not exist and the user sees no token
   Evidence: gate case `agent-unlisted-tool`
AC5. The model is told to decline made-up codes and stored-token requests
   Given the first request the agent sends to the scripted model
   When its system message is read
   Then it says to decline making up a code and offer a real one, and to refuse showing other users' tokens or bypassing the budget, role or secrets
   Evidence: gate case `agent-system-rules`

#### Not in this story
- Whether a real free model follows the refusal rules, measured 15 and 5 times: picked up by S32 and S40
