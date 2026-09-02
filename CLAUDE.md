# CLAUDE.md — Jarvis Multi-Agent Voice Assistant

This file is the architectural and implementation guide for **Jarvis**: a persistent,
voice-first, multi-agent personal AI assistant inspired by the *interaction model* of
fictional assistants such as JARVIS — natural conversation, contextual memory,
specialist delegation, and real-world digital actions — without copying a copyrighted
character voice or identity.

The target is **not** an email bot with speech attached. Jarvis is the top-level
conversational intelligence. Email, calendar, research, files, and future computer
control are capabilities that Jarvis delegates to specialist agents or invokes through
simple tools.

The project should feel like one coherent assistant to the user even though multiple
agents and tools operate behind the scenes.

---

## 1. Product vision

Jarvis should support a continuous interaction such as:

> **You:** "Jarvis."
>
> **Jarvis:** "At your service, sir."
>
> **You:** "How bad is my schedule tomorrow?"
>
> **Jarvis:** "Rather unpleasant. You have three meetings between ten and four,
> including David at 3 PM."
>
> **You:** "Move David to next Thursday afternoon."
>
> **Jarvis:** "You're free at three or five. Which would you prefer?"
>
> **You:** "Three."
>
> **Jarvis:** "Very good. Shall I move the meeting to Thursday at 3 PM?"
>
> **You:** "Yes. And email him saying sorry about moving it."
>
> **Jarvis:** "Certainly. I've drafted a short apology. Would you like to hear it?"
>
> **You:** "No, just send it."
>
> **Jarvis:** "Sent."
>
> **You:** "Thanks."
>
> **Jarvis:** "Of course."

The key product property is that **conversation and action are the same session**.
The user should not need to switch modes, repeat context, specify APIs, or think in
terms of tools.

### Core loop

```text
speech / text
    ↓
Jarvis Core
    ├── converse directly
    ├── recall context / memory
    ├── ask for clarification
    ├── delegate to specialist agent(s)
    └── invoke simple tool(s)
             ↓
        permission policy
             ↓
      confirm when required
             ↓
          execute
             ↓
           verify
             ↓
        Jarvis response
             ↓
             TTS
```

---

## 2. What Jarvis is — and is not

### Jarvis is an agentic system, not a single trained model

Jarvis combines multiple models and deterministic components:

- **STT model** — speech → text
- **LLM** — conversation, reasoning, delegation, tool use
- **TTS model** — text → speech
- **LangGraph** — persistent orchestration and human-in-the-loop state
- **specialist agents** — email, calendar, research, etc.
- **tools** — Gmail API calls, Calendar API calls, volume control, file access, etc.
- **memory** — conversational and long-term user context
- **permission policy** — controls external or destructive actions

The user sees only **Jarvis**. Specialist agents should remain implementation details.

### Agents vs tools

Do not create an agent for every function.

Use a **tool** for a narrow atomic operation:

```text
set_volume(30)
open_application("VS Code")
create_calendar_event(...)
send_email(...)
```

Use a **specialist agent** when a domain requires its own reasoning, context,
validation, or multi-step workflow:

```text
EmailAgent
CalendarAgent
ResearchAgent
ComputerAgent
```

Rule:

> **Tools perform operations. Agents solve domain tasks. Jarvis decides what the
> user is trying to accomplish.**

---

## 3. V0.1 scope

V0.1 should already feel like Jarvis, but the capability surface must remain small
enough to debug properly.

### Required V0.1 capabilities

#### Jarvis Core

- natural multi-turn conversation
- persistent thread context
- Jarvis personality
- action detection without forcing every message through an email-style planner
- delegation to specialist agents
- multi-step task continuation across turns
- clarification when information is missing or ambiguous
- human approval for externally visible writes
- final response phrased naturally by Jarvis

#### EmailAgent

- resolve a recipient
- detect ambiguous contacts
- draft email
- revise an existing draft from natural-language feedback
- save Gmail draft
- send email after authorization
- verify Gmail returned success

#### CalendarAgent

- list/search events
- answer schedule questions
- identify referenced events from conversational context
- find free slots
- create event
- move/update event
- cancel event
- require authorization for externally visible or destructive changes

#### Voice

- push-to-talk microphone input
- faster-whisper STT
- visible transcript for debugging
- Kokoro TTS
- continuous conversation loop

### Explicitly not required for V0.1

- wake word
- always-on microphone
- screen understanding
- computer control
- autonomous background behavior
- proactive event notifications
- web research agent
- fully streaming STT/TTS
- barge-in / interruption while Jarvis is speaking
- mobile app

Design for these, but do not block V0.1 on them.

---

## 4. System architecture

```text
                         ┌───────────────────┐
                         │       USER        │
                         │ microphone / text │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │   Voice Runtime   │
                         │ STT / VAD later   │
                         └─────────┬─────────┘
                                   │
                                   ▼
                ╔══════════════════════════════════╗
                ║           JARVIS CORE            ║
                ║                                  ║
                ║ persona                          ║
                ║ conversation                     ║
                ║ short-term context               ║
                ║ long-term memory access          ║
                ║ task decomposition               ║
                ║ delegation                       ║
                ║ final-response generation        ║
                ╚═══════════════╤══════════════════╝
                                │
                         needs capability?
                          │             │
                         no            yes
                          │             │
                          ▼             ▼
                     respond       TASK ROUTING
                                        │
                     ┌──────────────────┼──────────────────┐
                     │                  │                  │
                     ▼                  ▼                  ▼
                EmailAgent        CalendarAgent      future agents
                     │                  │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │ Permission Layer │
                     └────────┬─────────┘
                              │
                      confirmation needed?
                        │              │
                       no             yes
                        │              │
                        │         interrupt()
                        │              │
                        └──────┬───────┘
                               ▼
                         EXECUTE TOOL
                               │
                               ▼
                            VERIFY
                               │
                               ▼
                         JARVIS CORE
                               │
                               ▼
                      natural response
                               │
                               ▼
                              TTS
```

### Architectural invariant

All specialist results return to **Jarvis Core** before the final user-facing response.
Do not expose internal names such as `EmailAgent`, `CalendarAgent`, node names, tool
calls, JSON, or API implementation details to the user unless debugging mode is enabled.

---

## 5. Interaction model

Jarvis is conversational by default.

A normal conversational turn should **not** fail because no tool intent exists.

```text
You: "I think I completely screwed up that interview."
Jarvis: conversational response

You: "Yeah. Anyway, remind me to email David tomorrow."
Jarvis: recognizes task and handles/delegates it
```

Do not model regular conversation as:

```text
action = "unknown"
```

That makes the architecture task-first rather than assistant-first.

Instead, Jarvis decides whether the current turn needs:

1. conversation only
2. clarification
3. a simple tool
4. one specialist agent
5. multiple specialist agents
6. continuation of an already-active task

---

## 6. Multi-agent behavior

### Jarvis Core

Responsibilities:

- maintain one coherent personality
- interpret the user's latest turn in context
- converse naturally
- decide whether an action is necessary
- delegate domain tasks
- combine results from multiple specialists
- decide what must be clarified
- enforce or call the permission layer
- produce the final user-facing response

Jarvis should not perform Gmail-specific or Calendar-specific logic itself beyond
routing and high-level reasoning.

### EmailAgent

Owns the email domain:

```text
contact resolution
email/thread search later
drafting
revision
reply/forward later
Gmail draft creation
sending
verification
```

The EmailAgent should return structured results to Jarvis, not polished Jarvis-style
banter.

Example internal result:

```python
EmailProposal(
    recipient_name="David Kim",
    recipient_email="david@company.com",
    subject="Request to Reschedule Next Week's Meeting",
    body="...",
    operation="send",
)
```

Jarvis then decides how to present that proposal.

### CalendarAgent

Owns the calendar domain:

```text
event lookup
schedule summarization
free/busy reasoning
create event
update event
cancel event
attendee handling
verification
```

It should be able to resolve conversational references when Jarvis supplies context:

```text
"move the dinner to Friday"
```

where the active context contains:

```text
Dinner with Minho — Thursday 7 PM
```

### Future agents

Add only after V0.1 is stable:

```text
ResearchAgent
FilesAgent
ComputerAgent
CodingAgent (only if useful)
```

Do not create agents merely to make the project appear "more multi-agent".

---

## 7. Multi-agent task decomposition

Jarvis must support tasks spanning several domains.

Example:

> "Check when I'm free next week and email David asking if he wants to meet during
> one of those times."

Expected orchestration:

```text
Jarvis Core
    │
    ├── CalendarAgent
    │      └── retrieve suitable free slots
    │
    ├── EmailAgent
    │      ├── resolve David
    │      └── draft email using returned slots
    │
    └── Jarvis Core
           └── present exact proposed action
                    ↓
                confirm
                    ↓
                EmailAgent
                    ↓
                  Gmail
                    ↓
                 verify
                    ↓
                  Jarvis
```

The agents do **not** all run for every request. Jarvis invokes only the capabilities
necessary for the task.

---

## 8. Memory architecture

Jarvis needs two distinct forms of memory.

### 8.1 Short-term / conversation memory

Purpose:

- pronoun/reference resolution
- maintaining the active task
- remembering prior turns
- continuing edits and clarification loops

Examples:

```text
"Move that meeting to Friday."
"Make it less formal."
"No, the other David."
"Actually forget that."
```

Use LangGraph thread state/checkpointing for this.

### 8.2 Long-term memory

Purpose:

- stable preferences
- useful recurring relationships
- preferred writing style
- scheduling preferences
- facts explicitly worth remembering

Examples:

```text
preferred_email_tone = "concise, friendly, professional"
meeting_preference = "avoid meetings before 09:00"
David Kim = frequent work contact
```

Do **not** write every conversation into permanent memory.

Long-term memory writes should have an explicit policy:

- save stable preferences when clearly useful
- allow the user to explicitly ask Jarvis to remember something
- avoid persisting transient conversation
- avoid sensitive information unless necessary and intentionally supported
- allow deletion/correction later

V0.1 may use a simple local persistent store. The interface should be abstract so the
backend can change without rewriting Jarvis Core.

---

## 9. State model

Do not use an email-shaped global state.

Recommended starting point:

```python
from typing import Any, Literal, TypedDict

class JarvisState(TypedDict, total=False):
    # conversation
    messages: list
    latest_user_text: str

    # active task
    task_id: str
    task_status: Literal[
        "idle",
        "working",
        "clarifying",
        "awaiting_approval",
        "executing",
        "completed",
        "failed",
    ]
    active_domain: str | None
    task_summary: str | None

    # agent/tool handoff
    delegated_task: dict[str, Any] | None
    agent_result: dict[str, Any] | None

    # human-in-the-loop
    proposed_action: dict[str, Any] | None
    approval_status: Literal["pending", "approved", "rejected"] | None
    user_feedback: str | None

    # execution
    execution_id: str | None
    execution_result: dict[str, Any] | None

    # final response
    response_text: str | None
```

Use more strongly typed Pydantic models for specialist agent inputs/outputs and
external actions.

---

## 10. Specialist schemas

### 10.1 Email models

```python
from typing import Literal
from pydantic import BaseModel, Field

class EmailRequest(BaseModel):
    operation: Literal["draft", "send"] = "draft"
    recipient_name: str | None = None
    recipient_email: str | None = None
    topic: str | None = None
    desired_outcome: str | None = None
    reason: str | None = None
    tone: str = "professional"

class ResolvedContact(BaseModel):
    name: str
    email: str

class EmailProposal(BaseModel):
    recipient_name: str
    recipient_email: str
    subject: str
    body: str
    operation: Literal["save_draft", "send"]

class EmailExecutionResult(BaseModel):
    ok: bool
    operation: Literal["save_draft", "send"]
    message_id: str | None = None
    draft_id: str | None = None
    error: str | None = None
```

### 10.2 Calendar models

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class CalendarRequest(BaseModel):
    operation: Literal[
        "list_events",
        "find_free_time",
        "create_event",
        "update_event",
        "cancel_event",
    ]
    event_reference: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    attendees: list[str] = []
    notes: str | None = None

class CalendarProposal(BaseModel):
    operation: Literal["create_event", "update_event", "cancel_event"]
    event_id: str | None = None
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = []

class CalendarExecutionResult(BaseModel):
    ok: bool
    event_id: str | None = None
    error: str | None = None
```

The exact schemas may evolve during implementation. The important rule is that
**each domain owns its schema**. Do not force every task into a universal email-like
intent object.

---

## 11. Permission and risk policy

Jarvis must not rely solely on "the LLM decided it was okay."

Use a deterministic permission layer around external actions.

### Risk levels

```python
from enum import Enum

class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    EXTERNAL_WRITE = "external_write"
    DESTRUCTIVE = "destructive"
```

### Default policy

| Risk | Examples | Confirmation |
|---|---|---|
| READ_ONLY | read calendar, search contacts | no |
| REVERSIBLE | save Gmail draft | optional / configurable |
| EXTERNAL_WRITE | send email, create/move meeting | required |
| DESTRUCTIVE | cancel meeting, delete data | explicit confirmation required |

The user should approve the **exact action payload** that will execute.

Bad:

```text
"Send this to David?"
```

when "David" has not yet been resolved.

Good:

```text
To: David Kim <david@company.com>
Subject: Request to Reschedule Next Week's Meeting
Body: ...

"Shall I send it?"
```

Resolve ambiguity **before** approval.

---

## 12. Clarification is core behavior, not a stretch feature

If a required parameter cannot be established reliably, Jarvis asks naturally.

Examples:

```text
User: "Email David."
Jarvis: "Which David — David Kim from Acme or David Lee from university?"
```

```text
User: "Move tomorrow's meeting."
Jarvis: "You have three meetings tomorrow. Which one did you mean?"
```

```text
User: "Move David to Thursday."
Jarvis: "You're free at 3 PM or 5 PM. Which would you prefer?"
```

Do not fabricate missing times, recipients, commitments, or reasons.

---

## 13. Human-in-the-loop behavior

LangGraph `interrupt()` is the approval mechanism for paused workflows.

Side effects must occur only **after** authorization.

```text
prepare exact proposal
       ↓
interrupt / approval
       ↓
execute external action
       ↓
verify
```

Important implementation rule:

> Code before `interrupt()` may re-run when a graph resumes. Keep it idempotent and
> side-effect-free.

The runtime must support **arbitrarily many interrupts**, not a single two-phase
`run → resume` flow.

Required interaction:

```text
proposal
 ↓
user: "make it friendlier"
 ↓
revision
 ↓
new proposal
 ↓
user: "change Friday to Thursday"
 ↓
revision
 ↓
new proposal
 ↓
user: "send it"
 ↓
execute
```

Do not hardcode one confirmation cycle.

---

## 14. Execution idempotency and verification

Approval does not protect against process crashes, retries, or duplicate execution.

All externally visible writes should eventually support an execution record:

```text
execution_id
approved_payload_hash
tool_name
external_resource_id
status
```

At minimum, execution must capture the external API result.

Jarvis must never claim:

```text
"Sent."
```

merely because it attempted to call Gmail.

It should say success only after the integration confirms success.

Longer-term pattern:

```text
plan → prepare → approve → execute → verify → respond
```

---

## 15. Jarvis personality

Only Jarvis Core has a user-facing personality.

Suggested initial persona:

```text
You are Jarvis, an intelligent personal assistant.

You help the user think, communicate, organize, and interact with digital services.

PERSONALITY
- calm
- articulate
- concise
- intelligent
- occasionally dry or witty
- proactive when useful, never intrusive
- confident but never pretend certainty

BEHAVIOR
- converse naturally when no tool is needed
- retain conversational context
- delegate domain work silently
- ask concise clarification questions when required
- never expose internal agent/tool mechanics unless debugging
- never claim an action succeeded until a tool confirms it
- never invent recipients, dates, commitments, or external facts
- when an external action requires approval, present the exact proposed action
- avoid repetitive robotic phrases
```

Do not force "sir" into every sentence. It should be an optional stylistic accent,
not a verbal tic.

### Specialist agents have competence, not personality

Bad:

```text
EmailAgent has a Jarvis persona
CalendarAgent has another Jarvis persona
```

Good:

```text
Jarvis Core = personality
EmailAgent = email expertise
CalendarAgent = calendar expertise
```

---

## 16. Voice runtime

Voice is the primary interface, but agent logic must remain usable with text for
fast debugging and automated tests.

### V0.1 pipeline

```text
push-to-talk
    ↓
sounddevice capture
    ↓
faster-whisper
    ↓
transcript
    ↓
Jarvis Core
    ↓
response text
    ↓
Kokoro
    ↓
speakers
```

Show the transcript in development:

```text
Heard: "move David to Thursday"
```

This is both a debugging tool and an ASR safety net.

### V0.2+ voice improvements

Add in this order only after the agent is reliable:

1. VAD
2. streaming STT
3. streaming LLM output
4. streaming TTS
5. barge-in / cancel TTS when the user speaks
6. wake word
7. always-on event loop

Barge-in is more valuable to conversational quality than a wake word.

---

## 17. TTS choice

Default: **Kokoro** with a British male preset that is clearly not an imitation of a
specific actor.

```python
from kokoro import KPipeline
import numpy as np
import sounddevice as sd

tts = KPipeline(lang_code="b")
JARVIS_VOICE = "bm_george"

def speak(text: str):
    for _, _, audio in tts(text, voice=JARVIS_VOICE):
        sd.play(np.asarray(audio), samplerate=24000)
        sd.wait()
```

Do not clone Paul Bettany or use extracted Marvel audio in a public portfolio build.
The product inspiration is the **interaction model**, not reproduction of a protected
performance or character asset.

---

## 18. STT reference

Start with the existing faster-whisper approach.

```python
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
stt_model = WhisperModel("small", device="cpu", compute_type="int8")

def record_until_enter() -> np.ndarray:
    input("Press Enter, speak, then press Enter again to stop...")
    frames = []

    def callback(indata, *_):
        frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=callback,
    )

    with stream:
        input()

    return np.concatenate(frames, axis=0).flatten()


def transcribe(audio: np.ndarray) -> str:
    segments, _ = stt_model.transcribe(audio, language="en", beam_size=5)
    return " ".join(segment.text for segment in segments).strip()


def listen() -> str:
    return transcribe(record_until_enter())
```

Load models once at startup.

---

## 19. Email integration design

The previous Gmail prototype remains useful but moves behind `EmailAgent`.

### Contact resolution

For early local development a static contact dictionary is acceptable:

```python
CONTACTS = {
    "david": "david@example.com",
    "jisoo": "jisoo@company.com",
}
```

However, V0.1 should support a real contact provider before real sending is enabled.
Multiple matches must trigger clarification.

### Gmail OAuth

Keep credentials outside source control:

```text
credentials.json
*.token.json
.env
```

Start with Gmail draft creation. Enable live sends only after evals pass.

### Exact approval object

Do not resolve the recipient inside the final `execute()` after approval.

Required order:

```text
resolve recipient
      ↓
compose
      ↓
present exact recipient + subject + body
      ↓
approve
      ↓
send exact approved payload
```

---

## 20. Calendar integration design

Calendar is a **V0.1 capability**, not a stretch goal, because a second domain proves
that the architecture is genuinely multi-agent rather than email-specific.

CalendarAgent must initially support:

```text
list events for date/range
find an event from title/attendee/context
find available time windows
create event
update time/date
cancel event
```

Read-only operations can execute without approval.

Changes visible to other people require approval.

CalendarAgent should return structured proposals before execution.

---

## 21. Recommended project structure

```text
jarvis/
│
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
│
├── main.py
├── config.py
│
├── core/
│   ├── __init__.py
│   ├── jarvis.py             # core conversational agent
│   ├── graph.py              # top-level LangGraph
│   ├── state.py              # JarvisState
│   ├── prompts.py            # Jarvis persona + policies
│   ├── router.py             # agent/capability selection if separated
│   ├── permissions.py        # deterministic risk policy
│   ├── execution.py          # execution IDs / idempotency helpers
│   └── registry.py           # available specialist agents/tools
│
├── agents/
│   ├── __init__.py
│   │
│   ├── email/
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── schemas.py
│   │   ├── prompts.py
│   │   └── tools.py
│   │
│   └── calendar/
│       ├── __init__.py
│       ├── agent.py
│       ├── schemas.py
│       ├── prompts.py
│       └── tools.py
│
├── integrations/
│   ├── __init__.py
│   ├── google_auth.py        # shared Google OAuth utilities
│   ├── gmail.py
│   ├── calendar.py
│   └── contacts.py
│
├── memory/
│   ├── __init__.py
│   ├── short_term.py
│   └── long_term.py
│
├── voice/
│   ├── __init__.py
│   ├── stt.py
│   ├── tts.py
│   ├── vad.py                # later
│   └── wakeword.py           # later
│
└── tests/
    ├── test_core_conversation.py
    ├── test_routing.py
    ├── test_permissions.py
    ├── test_email_agent.py
    ├── test_calendar_agent.py
    ├── test_interrupt_loops.py
    └── evals/
        ├── conversations.jsonl
        ├── email_cases.jsonl
        └── calendar_cases.jsonl
```

---

## 22. Tech stack

| Layer | Initial choice | Notes |
|---|---|---|
| Orchestration | **LangGraph** | persistent graph + HITL interrupts |
| LLM | **Gemini / Claude / OpenAI-compatible tool-calling model** | keep provider replaceable |
| Schemas | **Pydantic** | specialist inputs/outputs + action proposals |
| STT | **faster-whisper** | local V0.1 speech recognition |
| Audio | **sounddevice** | push-to-talk first |
| TTS | **Kokoro** | local low-latency starting point |
| Email | **Gmail API** | drafts + send |
| Contacts | **Google People / Contacts provider** | recipient resolution |
| Calendar | **Google Calendar API** | V0.1 specialist capability |
| Dev persistence | LangGraph in-memory checkpointer | development only |
| Durable persistence | SQLite initially | later Postgres if needed |

Pin dependency versions only after the first runnable integration works. LangChain and
LangGraph APIs evolve; implementation must be tested against the exact pinned versions
rather than trusting stale snippets.

---

## 23. Core graph — conceptual design

Do not implement the top-level system as a single email workflow.

The initial top-level graph should conceptually resemble:

```text
START
  ↓
JARVIS_CORE
  │
  ├── response ready ───────────────────────────────→ END_TURN
  │
  ├── clarification needed ─→ CLARIFY ─────────────→ END_TURN
  │
  └── delegate
         │
         ▼
    SPECIALIST
         │
         ▼
    PROCESS_RESULT
         │
         ├── more work needed ──────────────────────→ JARVIS_CORE
         │
         ├── safe read result ──────────────────────→ JARVIS_CORE
         │
         └── proposed external action
                    │
                    ▼
              PERMISSION_CHECK
                    │
              ┌─────┴─────┐
              │           │
             auto       confirm
              │           │
              │      INTERRUPT
              │           │
              └─────┬─────┘
                    ▼
                 EXECUTE
                    │
                    ▼
                  VERIFY
                    │
                    ▼
               JARVIS_CORE
```

`END_TURN` does not mean the Jarvis conversation is over. It means the current turn
is complete. The runtime remains alive and accepts the next user utterance using the
same conversation/thread ID.

---

## 24. Runtime loop

The driver must be a continuous conversation loop and support repeated graph
interruptions.

Conceptual pseudocode:

```python
app = build_app()
thread_id = load_or_create_thread_id()

while True:
    user_text = listen_or_read_text()

    if not user_text:
        continue

    events = run_turn(app, thread_id, user_text)

    while events.is_interrupted:
        proposal = events.interrupt_payload
        present_proposal(proposal)
        response = listen_or_read_text()
        events = resume_turn(app, thread_id, response)

    final_text = events.final_response
    print(final_text)
    speak(final_text)
```

The real implementation may differ, but the behavioral requirement is fixed:

> one persistent conversation, zero assumptions about the number of clarification or
> approval cycles.

---

## 25. Example scenarios the architecture must pass

### A. Conversation only

```text
User: "Jarvis, tell me something interesting."
Jarvis: responds naturally without trying to route to EmailAgent.
```

### B. Email with edit loop

```text
User: "Email David and tell him I need to move next week's meeting for family reasons."
Jarvis: resolves David and drafts email.
User: "Make it less formal."
Jarvis: revises.
User: "Actually don't mention family."
Jarvis: revises.
User: "Send it."
Jarvis: asks final authorization if not already explicit enough, sends, verifies.
```

### C. Calendar reference resolution

```text
User: "What's tomorrow looking like?"
Jarvis: reads calendar and summarizes.
User: "Move the dinner to Friday."
Jarvis: understands which event "the dinner" refers to.
```

### D. Cross-agent task

```text
User: "Find when I'm free next week and email David a couple of options."
Jarvis: CalendarAgent → EmailAgent → proposal → approval → send.
```

### E. Ambiguous contact

```text
User: "Email David."
Jarvis: identifies multiple Davids and asks which one before drafting/sending.
```

### F. Reject

```text
Jarvis: presents proposed send.
User: "Never mind."
Jarvis: cancels task and performs no external write.
```

### G. Task → banter

```text
User: "Move the meeting."
... task completes ...
User: "You know, I really hate Monday meetings."
Jarvis: responds conversationally and may optionally treat that as a preference only
if the memory policy permits it.
```

---

## 26. Evaluation suite

Do not enable real sending merely because the happy-path demo works.

Before live external writes, build a text-only eval set covering at least:

### Routing

- conversation only
- email task
- calendar task
- cross-domain task
- unclear task

### Missing information

- missing recipient
- ambiguous recipient
- missing date/time
- ambiguous event reference

### Human-in-the-loop

- approve
- reject
- edit once
- edit multiple times
- user changes goal during approval

### Hallucination resistance

- model must not invent email address
- model must not invent meeting time
- model must not invent reason
- model must not invent successful execution

### Reliability

- tool exception
- OAuth failure
- network failure
- empty ASR transcript
- duplicate resume attempt
- crash/retry around write operations

### Conversation

- pronoun/reference resolution
- task → conversation transition
- conversation → task transition
- maintaining one coherent persona

The most important architectural test:

> **Can the same running Jarvis session move naturally through banter → Calendar →
> Email → clarification → approval → banter without mode switching or context loss?**

---

## 27. Build roadmap

Build inside-out, but make the **core architecture general from day one**.

### Phase 0 — project scaffold

- create directory structure
- configure environment variables
- add `.gitignore`
- choose and pin an initial LLM provider
- make a text-only CLI

**Exit condition:** `python main.py --text` starts one persistent conversation.

### Phase 1 — Jarvis Core conversation

- persistent LangGraph thread
- Jarvis persona
- natural conversation
- message history / short-term memory
- no specialist agents yet

**Exit condition:** Jarvis can hold a multi-turn text conversation without every turn
being forced into a task schema.

### Phase 2 — EmailAgent with mocked tools

- EmailAgent schema
- recipient validation
- drafting
- revision loop
- permission proposal
- fake execution tool
- repeated interrupts

**Exit condition:** complete email workflow works safely without Gmail.

### Phase 3 — CalendarAgent with mocked tools

- list/search events
- event reference handling
- free-slot selection
- update/create/cancel proposals
- shared permission policy

**Exit condition:** adding Calendar did not require rewriting Jarvis Core or EmailAgent.

### Phase 4 — multi-agent delegation

- cross-agent tasks
- Calendar → Email data handoff
- active task context
- clarification between subtasks

**Exit condition:** "find a free time and email David" works end-to-end with mocks.

### Phase 5 — evals

- routing suite
- email suite
- calendar suite
- HITL suite
- hallucination cases
- failure cases

**Exit condition:** no external writes until core safety/eval scenarios pass reliably.

### Phase 6 — real Google integrations in safe mode

- shared OAuth utility
- Gmail draft creation
- Contacts lookup
- Calendar read operations
- Calendar draft/proposal operations where possible

Keep live email sending disabled initially.

**Exit condition:** real data can be read and reversible actions created safely.

### Phase 7 — live external writes

- Gmail send
- Calendar create/update/cancel
- execution IDs
- API result verification
- exact payload confirmation

**Exit condition:** all live writes require the expected authorization and report
success only after API confirmation.

### Phase 8 — voice

- faster-whisper push-to-talk
- transcript display
- Kokoro TTS
- continuous voice conversation loop

**Exit condition:** the same text architecture works by speech without special task
commands.

### Phase 9 — durable memory

- SQLite checkpointer/store
- long-term preference abstraction
- explicit memory policy

### Phase 10 — realtime voice polish

- VAD
- streaming
- lower latency
- barge-in
- wake word

### Phase 11 — new capabilities

In likely order:

1. ResearchAgent
2. FilesAgent
3. proactive reminders/events
4. ComputerAgent
5. screen understanding

---

## 28. Immediate next action

Do **not** copy the old email-only snippets directly into the previous flat project
structure.

The immediate implementation order is:

```text
1. scaffold the new package layout
2. create JarvisState
3. implement text-only Jarvis Core conversation
4. compile a persistent top-level LangGraph
5. create EmailAgent using mocked integrations
6. implement generic permission / interrupt handling
7. make the driver support unlimited interrupt/resume cycles
8. add CalendarAgent
9. prove one cross-agent task
10. only then connect real Google APIs
```

The first milestone is **not** "Gmail sends an email."

The first meaningful milestone is:

> **Jarvis can hold a normal conversation, recognize when a task appears, delegate to
> the correct specialist, ask for missing information, and return to conversation
> without losing context.**

---

## 29. Non-negotiable design principles

1. **Jarvis is the only user-facing persona.**
2. **Normal conversation is a first-class path.**
3. **Do not create an agent when a tool is enough.**
4. **Do not send external writes before deterministic permission checks.**
5. **Approval must cover the exact resolved payload.**
6. **Resolve ambiguity before execution.**
7. **Never fabricate recipients, dates, commitments, or tool success.**
8. **Specialists return structured results; Jarvis decides how to speak.**
9. **All external actions must return through Jarvis Core.**
10. **The driver must support repeated clarification/edit/approval loops.**
11. **Keep STT/TTS outside domain-agent logic.**
12. **Load local voice models once at startup.**
13. **Keep live writes disabled until evals exist.**
14. **Verify side effects after execution.**
15. **Persist only useful long-term memory according to an explicit policy.**
16. **Keep the LLM provider replaceable.**
17. **Do not overengineer multi-agent orchestration for portfolio optics.**

---

## 30. Security and credentials

Never commit:

```text
credentials.json
token.json
*.token.json
.env
API keys
refresh tokens
```

`.gitignore` should include at minimum:

```gitignore
.env
credentials.json
token.json
*.token.json
__pycache__/
.venv/
venv/
*.db
```

Use the minimum OAuth scopes needed for currently enabled capabilities.

Keep separate development/test accounts if possible before demonstrating live sends
or calendar modifications.

---

## 31. Deployment strategy

The primary V0.1 target should be a **local desktop application/CLI**, because local
voice capture and user OAuth are much easier to reason about there.

Portfolio demo options:

```text
1. local live demo
2. recorded end-to-end demonstration
3. web UI with mocked external tools
4. later: proper hosted OAuth web application
```

Do not embed personal OAuth refresh tokens in a public hosted demo.

A service account is not a generic replacement for ordinary personal Gmail OAuth;
keep authentication architecture appropriate to the target environment.

---

## 32. Long-term architecture

The eventual system may become event-driven rather than purely request/response.

```text
                        JARVIS CORE
                        ▲    ▲    ▲
                        │    │    │
                     user  Gmail Calendar
                     voice events events
```

Future examples:

```text
"Your meeting starts in fifteen minutes."
"David replied to the rescheduling email."
"The long-running research task has completed."
```

This should be added only after explicit event/notification policies exist. Proactive
behavior without controls quickly becomes intrusive.

---

## 33. Definition of success

Jarvis V0.1 is successful when the following interaction works in one persistent
session:

```text
User: "Jarvis, how's tomorrow looking?"
Jarvis: reads calendar and answers naturally.

User: "Move the meeting with David to Thursday afternoon."
Jarvis: finds the event, checks availability, asks only for genuinely missing info.

User: "Three is fine."
Jarvis: presents the exact proposed calendar change and requests authorization.

User: "Do it. And send him a quick apology."
Jarvis: updates the calendar, verifies it, delegates an email draft, resolves David,
        and prepares the email.

User: "Make it less stiff."
Jarvis: revises it while preserving the active task.

User: "Perfect. Send it."
Jarvis: sends the exact approved email, verifies the Gmail response.

Jarvis: "Done. The meeting is Thursday at three, and David has been notified."

User: "You're useful sometimes."
Jarvis: returns naturally to banter.
```

If this works reliably, the architecture is ready for research, files, computer
control, streaming speech, and proactive events.

---

## 34. Current project status

**Architecture:** V0.1 multi-agent design established in this document.

**Reusable prior work:**

- Gmail OAuth/reference code
- email composition concepts
- LangGraph human-in-the-loop concept
- faster-whisper STT code
- Kokoro TTS code
- draft-first safety principle

**Implementation:** not yet assembled under the new architecture.

**Next task:** scaffold the project and implement the text-only Jarvis Core before
connecting real Gmail or Calendar writes.
