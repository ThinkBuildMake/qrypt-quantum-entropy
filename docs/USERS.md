# USERS.md — Quantum Entropy MCP Server & Secure Token Agent

## Scope

**This document covers:** who the product is for and who it is not for; what goes wrong for them today, with sources; the moments the product enters their day; the use cases it serves and why this shape of product is the one they'd choose; the minimum first iteration and what comes after; what is out of scope; and the constraints the project was given.

**This document does not cover:** how the product is built, verified, measured or hosted. Requirements, key metrics, stack and architecture are separate documents that trace back to the use case IDs (UC-n) and the constraints here.

**Status:** Draft. Interview with the project owner, 2026-10-17. Items marked *(open)* were not answered and are listed in `USERS_handoff.md`.

---

## 1. Target user

**Priya, an agent developer who is wiring an AI agent to hand out credentials (reset codes, API keys, session IDs) and has to defend where those values come from.**

Priya builds an agent that talks to end users and, on request, issues something that must be unpredictable. She decides how the agent gets its random values, connects it, and later answers questions about it. She is responsible for the agent never handing out a guessable value. Her last experience with this (the project owner's own) was to call `Math.random()`, say "pseudorandom is fine," and get no pushback.

**Secondary user: Sam, the operator.** Sam owns the shared entropy allotment and the list of clients allowed to draw from it. He needs to see who is using how much, and to know that when the allotment is gone the product refuses instead of improvising.

**Not the target user:**
- **Jonas, the end user** who asks the agent for a reset code. He sees only a code or a refusal; he never touches the product. He is a user of Priya's agent.
- **A security reviewer** (Dana). She reads the audit trail Priya produces but does not operate the product.

---

## 2. The problem, in numbers

### Language models produce guessable "random" values

Irregular, an AI security firm, reportedly found that passwords generated directly by major chatbots measured about 20–27 bits of entropy, against roughly 98–120 bits for a truly random 16-character password. In one test of Claude, 50 requested passwords produced only 30 unique values, one string appearing 18 times. Irregular states the weakness cannot be fixed by prompting or temperature changes. (Figures from press coverage; the original report was not read. See Evidence limitations.)

### Non-cryptographic generators are a recognised weakness

`Math.random()` used for session tokens or keys is classed as CWE-338, and common static analysers flag it as high severity. The standard remedy is the operating system's cryptographic generator. This is a classification, not a measurement of harm; no named breach was found.

### What the owner's own experience adds

Priya's equivalent, the project owner, has not been in a review or questionnaire where "we use the OS generator" was rejected or where an audit trail of randomness was demanded *(open: no firsthand case found)*.

---

## 3. Why we target these moments

The product enters three moments of Priya's work: the moment an agent actually issues a value (core), and the moments before and after it where she sets up and proves the source (supporting). It does not enter the rest of her build. The goal is that no value an agent hands out is invented by the model, and that Priya can say where any one of them came from.

**Reason 1: the harm is at issuance, and it is invisible there.** A model-invented code looks fine to the end user and to Priya; the weakness appears only to an attacker (Section 2).

**Reason 2: the common fix is free.** An operating-system generator already removes the guessability. What it does not give Priya is a per-value record of where something came from. That gap is the only thing this product adds, and Section 5 defends it.

**Reason 3: the three moments are one thread.** What Priya connects (UC-2) is what issues the value (UC-1) and what she later traces (UC-3).

### Out of scope: the rest of her build

Choosing the agent's model, building the end-user interface, and deciding what a token is allowed to unlock are different work at different moments.

---

## 4. Workflow: where the product enters the day

**Tuesday, 2:00 PM.** Priya has just finished the first version of a support agent. Before connecting it, she opens the MCP Inspector, connects with her client key, and asks for a 6-digit code. She wants to see a value and where it came from. → **UC-2**

**The 30 seconds before opening it:** she doesn't yet know whether her agent will call a tool or just write a number itself.

**9:40 PM, Priya is asleep.** Jonas tells the agent: "Issue a one-time reset code for user 42 that expires in 10 minutes." The agent asks the product for a code instead of inventing one, stores it so it cannot be read back, and shows Jonas the code. Jonas redeems it once; a second try and a try after 10 minutes both fail. → **UC-1**

**9:55 PM.** The shared allotment runs out. The agent tells Jonas it cannot issue a code right now. It does not make one up. Sam sees, next morning, which client used the most. → **UC-4**

**Thursday, 11:00 AM.** A reviewer asks about a code issued a week ago. Priya finds the matching record, sees which client asked, when, how many bytes, and which upstream batch it came from, and sees that the record does not contain the code. → **UC-3**

---

## 5. Use cases

### UC-1: Issue a one-time value for an end user (core)

| | |
|---|---|
| **Trigger** | An end user asks Priya's agent for a code, key or identifier in plain language |
| **Input** | The request ("reset code for user 42, 10 minutes") |
| **What they need to see** | A value that came from the entropy source, with where it came from; or a clear refusal |
| **What they do with it** | Jonas redeems the code once; Priya's agent never invents an alternative |
| **Time available** | Jonas is waiting in a conversation; Priya is not present |
| **Must never** | Serve the same random bytes to two requests; issue a value the model made up; let a redeemed or expired code work; let a user ask the agent to reveal other users' codes; quietly substitute another source when the entropy source is unavailable or used up |

**Why a tool-calling product, not the OS generator or the model alone:**
A local OS generator already gives Priya unguessable values, and a model on its own does not (Section 2). If strength were the only need, a one-line local call would win.

The case rests on what that call does not give her:
1. **A boundary the model cannot cross.** The agent gets values only by calling a tool, so "the model made it up" is not possible by construction. A local wrapper could do this too; this is not unique.
2. **A record per value.** Each issuance is tied to who asked, when, how much entropy it used and which upstream batch supplied it. A bare local call leaves no such record unless she builds one.
3. **A named, mandated source.** The assessment requires this entropy service (Section 8).

The value is the per-value trail, not the strength. *(open: this weakens if reviewers accept a plain log over a local generator.)*

---

### UC-2: Connect and inspect (supporting)

| | |
|---|---|
| **Trigger** | Priya wants to know what the product offers and what a value looks like |
| **Input** | Her client key |
| **What they need to see** | The available tools with their expected inputs; for each call, the value plus its source and batch; a plain error for bad input |
| **What they do with it** | Decides whether to wire her agent to it; copies the connection snippet |
| **Time available** | An afternoon of setup |
| **Must never** | Accept an unknown client; crash on bad input instead of explaining it |

**Why a standard tool surface, not a custom script:** She can already write her own generator. A standard tool listing lets any agent framework she uses discover and call it without custom glue, and shows her what a returned value looks like before she commits. The advantage is discoverability, nothing deeper.

---

### UC-3: Trace an issued value to its source (supporting)

| | |
|---|---|
| **Trigger** | A reviewer or Priya asks where one issued value came from |
| **Input** | Which issuance (a reference, not the value) |
| **What they need to see** | Which client asked, which tool, when, how much entropy, which upstream batch |
| **What they do with it** | Answers the reviewer |
| **Time available** | Days; this is audit work, not live |
| **Must never** | Show or store the value itself; allow records to be changed or deleted |

**Why an audit record, not logs she writes herself:** Hand-written logs get edited, omit the batch, or accidentally contain the value. Whether a reviewer would ever demand this is unproven *(open)*; its worth is that it is cheap to produce and hard to fake.

---

### UC-4: See and cap entropy use (secondary user, Sam)

| | |
|---|---|
| **Trigger** | A budget alert, or Sam's routine check |
| **Input** | The admin view |
| **What he needs to see** | Total used against the allotment, and use per client; an empty, valid view on first run |
| **What he does with it** | Decides which client to question or cut off |
| **Time available** | A few minutes |
| **Must never** | Keep issuing after the allotment is gone; let a client draw without being identified |

**Why a meter, not just Qrypt's own account page:** Qrypt reports one account total. Sam needs to know *which of Priya's clients* spent it. *(open: Qrypt's portal usage view was not checked.)*

---

## 6. The minimum product

**First iteration.** The assessment requires all five core capabilities, so the minimum is all five; none can be cut and still be accepted. Priya would open it for UC-1 alone, but a reviewer-ready answer (UC-3) and a safe shared allotment (UC-4) are part of the same requirement.

| # | Feature | Use case | What it does for the user | Acceptable in v1 |
|---|---|---|---|---|
| 1 | Three tools: random bytes, random integer, token of a chosen kind | UC-1, UC-2 | Gives the agent values to hand out, each with its source and batch | Bad input explained, never a crash |
| 2 | A held supply of entropy that each byte leaves exactly once | UC-1 | Many simultaneous requests all get served | No byte ever given twice; supply runs low safely |
| 3 | A token agent that takes plain-language requests, stores codes unreadably with owner and expiry, and lets each code be redeemed once | UC-1 | Jonas gets a working one-time code | Redeems once; fails on reuse and after expiry |
| 4 | Per-client keys and a global allotment meter that refuses when empty | UC-2, UC-4 | Priya and Sam know who is drawing; nobody draws past the limit | Unknown client rejected; a clear message when empty |
| 5 | An append-only record of every issuance, without the value | UC-3 | Priya can trace a value to a batch | Cannot be edited; never contains a value |

**Later iterations.** The owner wants none of these now and has set no order.

| # | Feature | Use case | What it adds for the user | Why not first |
|---|---|---|---|---|
| 1 | Per-client daily quotas | UC-4 | Stops one client using the whole allotment | Not required to pass; needs core in use first |
| 2 | Remote (HTTP) connection with standard sign-in | UC-2 | Agents on other machines can connect | Not required; adds a hosting-terms question (Section 8) |
| 3 | Admin dashboard | UC-4 | Sam sees usage at a glance | Not required |
| 4 | A client example in another language | UC-2 | Proof it works beyond one stack | Not required |
| 5 | Optional fallback to the OS generator, clearly labelled | UC-1 | Keeps issuing when the entropy source is down | Not wanted by default; must be explicit and raised as an alert |

---

## 7. Explicitly out of scope

| Not doing | Why |
|---|---|
| Quietly falling back to another random source | Defeats the one thing Priya is buying: knowing where a value came from |
| Any way to read back issued codes | Priya's agent and Jonas never need them; it would let a prompt trick the agent into leaking others' codes |
| An end-user interface | Jonas talks to Priya's agent; the product has no screens for him |
| Offering entropy to outside parties or reselling it | Not the assessment's purpose; the Qrypt terms restrict it (Section 8) |
| Production use, real customer data, or commercial use | Assessment only (Section 8) |
| Proving quantum entropy beats the OS generator | Mandated, not argued; recorded as an open risk (Section 5, UC-1) |

---

## 8. Constraints we were given

| Constraint | What it is | Given by |
|---|---|---|
| Timeline | Due Tuesday 2026-10-20 at 11:59 PM Central (CST); ready for review Wednesday 2026-10-21. Nothing is to be cut; if short on time, no feature has been designated to drop first | Project owner |
| Team | One person | Project owner |
| Spend | Free tiers only; Qrypt use capped at 10,240 KiB total for the assessment; the cap is not shared with other projects; none used as of 2026-10-17 | Assessment brief; project owner |
| Services / vendors | Qrypt entropy is mandatory (the assessment is Qrypt's). The owner's preferred list is a suggestion, not a mandate: Anthropic, Aqua Voice, Cohere, Cursor, DigitalOcean, ElevenLabs, Google One, Midjourney, OpenAI, OpenRouter, Railway, Vultr, Whisper. The agent's language model is reached through OpenRouter | Assessment brief; project owner |
| Hardware in hand | None; the language model is an API | Project owner |
| Must integrate with | Any MCP-capable agent client; Qrypt's entropy REST service | Assessment brief |
| Intended use | Assessment only; not public, not commercial | Project owner |
| Regulatory / contractual | No regulation known. The Qrypt Terms of Use (updated 2025-08-05) apply: the licence is for "personal or … internal business purposes" (1.7a); making the software available as an internet or cloud service is barred (1.8a); redistributing or making available "Qrypt Properties" is barred (1.8c). The Terms are silent on caching entropy in memory. Neither the Terms nor the docs defines whether the random bytes themselves are "Qrypt Properties" *(open: confirm with Qrypt)* | Qrypt Terms of Use |

---

## 9. Evidence limitations

- **Irregular findings** come from press and blog coverage; the original report was not read, and its date is inconsistent across sources (one source says February 2026). It measures chatbots generating passwords directly, not agents calling tools.
- **CWE-338** is a weakness classification, not a measurement of harm; no named breach was found.
- **The PRD's "8 hours to 1 hour per review" and "$3,780 a year"** are the brief's own illustrative assumptions (6 reviews × 7 hours × $90). The owner's one firsthand experience did not match them.
- **No authoritative standard** was found that requires an audit trail for agent-generated secrets; the guidance is good practice only. One security audit of a remote secret-generating tool advised preferring local generation, which cuts against a remote source.
- **Qrypt pricing:** no public price list or free tier was found; the Terms say fees depend on a private order.
- **NIST SP 800-90B / FIPS 140-3** apply to entropy sources inside modules submitted for validation. Whether Qrypt's service is validated was not checked.
- None of these sources measures whether this product improves outcomes.

---

## References

1. TechSpot, "AI-generated passwords are surprisingly easy to crack, researchers find." https://www.techspot.com/news/111392-ai-generated-passwords-surprisingly-easy-crack-researchers-find.html
2. HackMag, "LLMs Generate Predictable Passwords." https://hackmag.com/news/llm-passwords
3. Field Effect, "Predictable credentials generated by AI tools." https://fieldeffect.com/blog/predictable-credentials-generated-ai-tools-risks
4. CWE-338, Use of Cryptographically Weak PRNG. https://hub.corgea.com/vulnerabilities/CWE-338
5. OWASP, Forgot Password Cheat Sheet. https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
6. Qrypt, Terms of Use (updated 2025-08-05). https://www.qrypt.com/terms/
7. Qrypt, Quantum Entropy docs. https://docs.qrypt.com/eaas/
8. NIST CMVP, entropy validations. https://csrc.nist.gov/Projects/cryptographic-module-validation-program/entropy-validations/documents
9. OpenRouter, privacy and logging. https://openrouter.ai/docs/features/privacy-and-logging
