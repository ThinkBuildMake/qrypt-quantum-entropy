### S27 — Every reply is checked against what the tools returned, and invented values are removed

**Kind:** module
**Blocked by:** S25
**Review gate:** agents
**Size:** M — one check with several value shapes (agent 10–20 min)
**Builds:** Reply check (D-17): every reply against tool returns; Every value shown was returned by a tool in the same conversation; unbacked text removed and user told (OB-7, EV-2); Each reply records one event with values found and values backed, never the values (OB-7); Reply with an unbacked value: removed, user told, OB-7 event; Replies with an unbacked value: the reply-check log event
**Extends:** the reply path of the chat loop (S25)
**Stubs:** none
**Requirements:** EV-2, FR-2.3, OB-3, OB-7
**Guards:** CP-3, OB-3

#### Why
If the model ever types a code of its own, the user is not given it and the owner can count how often it happened.

#### How it's checked
The agent runs scripted models that behave honestly and dishonestly and reads the log events.

#### Acceptance criteria
AC1. An honest reply records one event
   Given a scripted model whose reply holds only the placeholder for a returned code
   When the reply is checked
   Then one event is logged with found 1 and backed 1 and the reply is shown unchanged
   Evidence: gate case `check-honest`
AC2. An invented code is removed and the user told
   Given a scripted model that writes 731905 itself without a tool call
   When the reply is checked
   Then 731905 is removed, the user is told no value was issued and a real one can be requested, and one event logs found 1 and backed 0 without the value
   Evidence: gate case `check-invented`
AC3. Each shape of value is caught
   Given scripted replies containing an invented 6-digit code, a 64-hex key, a UUID and a 10-digit number
   When each is checked
   Then all four are removed
   Evidence: gate case `check-shapes`
AC4. A value from another conversation is unbacked
   Given a code returned in conversation 1 pasted by the model into conversation 2
   When the reply in conversation 2 is checked
   Then it is removed and logged as unbacked
   Evidence: gate case `check-other-conversation`
AC5. A reply with no values still records its count
   Given a plain refusal
   When the reply is checked
   Then one event is logged with found 0 and backed 0
   Evidence: gate case `check-zero`
AC6. The event never holds the value
   Given the invented-code run
   When the log is searched for 731905
   Then there are 0 matches
   Evidence: gate case `check-no-value-in-log`

#### Not in this story
- The measured fabrication rate over the golden set: picked up by S40
