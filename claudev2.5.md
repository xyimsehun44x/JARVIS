# claudev2.md — Jarvis Multi-Agent Voice Butler

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

### Product pillars

Jarvis is not merely voice control with speech output. The product should feel like a
**persistent AI butler** built from four equally important pillars:

```text
PERSONALITY + MEMORY + CAPABILITY + PRESENCE
     │           │          │          │
     │           │          │          └─ always available from the desktop
     │           │          └──────────── Gmail / Calendar / tools / agents
     │           └─────────────────────── context, preferences, continuity
     └─────────────────────────────────── calm voice, dry wit, natural banter
```

Conversation is not only an interface to tools. It is part of the product. Jarvis should
be able to move naturally between banter and action without changing modes:

```text
banter → calendar lookup → clarification → action proposal → approval → execution → banter
```

Humor should be contextual and understated, not a joke generator. Memory should make
banter more relevant over time, while capabilities make the assistant genuinely useful.

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

#### Desktop runtime

V0.1 should have a **development desktop shell** around the Python Jarvis backend:

- global push-to-talk shortcut while another app is focused
- system tray / macOS menu-bar presence
- small floating listening / thinking / speaking indicator ("orb")
- compact approval cards for consequential actions
- optional full conversation/history window
- microphone / TTS controls and basic settings
- backend remains runnable headlessly or from a text CLI for tests

The polished installer, code signing, automatic updates, wake word, and deep OS control
are later milestones. The desktop shell must not become a dependency of Jarvis Core.

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

Jarvis should run as a **desktop-resident client around a platform-independent Python
backend**. The app is the shell; Jarvis Core is the product logic.

```text
                    ┌──────────────────────────────┐
                    │       DESKTOP SHELL          │
                    │ Tauri 2 + web UI (preferred)│
                    │                              │
                    │ tray/menu bar                │
                    │ global hotkey                │
                    │ floating orb                 │
                    │ approval cards               │
                    │ chat/history/settings        │
                    └──────────────┬───────────────┘
                                   │ local IPC
                                   ▼
                    ┌──────────────────────────────┐
                    │      PYTHON JARVIS BACKEND   │
                    │                              │
 USER ─mic/text───▶ │ Voice Runtime / text input   │
                    │          │                   │
                    │          ▼                   │
                    │  FAST COMMAND GATE           │
                    │          │                   │
                    │ ┌────────┼───────────────┐   │
                    │ │        │               │   │
                    │ ▼        ▼               ▼   │
                    │Tier 0   Tier 1       Tier 2  │
                    │tool     direct LLM   Jarvis  │
                    │                     Core     │
                    │                       │      │
                    │               ┌───────┼──────┐
                    │               ▼       ▼      ▼
                    │            Email   Calendar future
                    │            Agent    Agent    agents
                    │               └───────┬──────┘
                    │                       ▼
                    │                Permission Layer
                    │                       │
                    │                  EXECUTE / VERIFY
                    │                       │
                    │                 Jarvis response
                    │                       │
                    │                      TTS
                    └───────────────────────┼───────────┘
                                            ▼
                                         speakers
```

The desktop shell may run on Windows and macOS using the same Jarvis backend. OS-specific
features must live behind adapters rather than leak into agents:

```text
SystemController
    ├── WindowsAdapter
    ├── MacOSAdapter
    └── LinuxAdapter (later)
```

### Architectural invariant

All specialist results return to **Jarvis Core** before the final user-facing response.
Do not expose internal names such as `EmailAgent`, `CalendarAgent`, node names, tool
calls, JSON, or API implementation details to the user unless debugging mode is enabled.

### 4.1 Low-latency execution tiers

Jarvis must **not** send every utterance through the full multi-agent graph. Voice
assistants feel intelligent only when common actions and banter begin responding quickly.
Use three execution tiers:

```text
TIER 0 — deterministic direct tool path
    examples: "open Spotify", "volume down", "stop speaking", explicit approval/reject
    implementation: regex/state match + allowlisted local tool
    LLM calls: 0

TIER 1 — direct conversational LLM path
    examples: banter, greetings, simple questions, conversational replies
    implementation: one direct model call using Jarvis persona + compact conversation context
    specialist agents: 0

TIER 2 — full Jarvis agent path
    examples: ambiguous requests, email/calendar work, multi-step tasks, cross-agent work
    implementation: Jarvis Core + specialists + permission layer + verification
```

The **Fast Command Gate must be deterministic and conservative**. If it is not highly
confident that an utterance maps to an allowlisted low-risk action, fall through to
Jarvis Core. Never guess an external side effect from a regex.

Fast-path execution must never bypass the existing safety policy:

- local reversible commands may execute immediately if explicitly allowlisted
- externally visible writes such as Gmail send or Calendar update still require the
  normal permission policy even if the intent itself is obvious
- destructive operations never use an unconfirmed direct-tool shortcut
- an active `awaiting_approval` state may recognize explicit approval/reject phrases
  deterministically, avoiding an unnecessary LLM call

This tiering is an optimization boundary, not a second personality. **Jarvis remains the
only user-facing persona** and may give a short TTS acknowledgement after a Tier-0 action.

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

Memory is a major part of the butler experience. Jarvis needs to distinguish **working
memory**, **semantic long-term memory**, **episodic memory**, and **external sources of
truth** rather than treating all historical information as one chat transcript.

### 8.1 Working / conversation memory

Purpose:

- pronoun/reference resolution
- maintaining the active task
- remembering recent turns
- continuing edits and clarification loops
- preserving pending proposals and approvals

Examples:

```text
"Move that meeting to Friday."
"Make it less formal."
"No, the other David."
"Actually forget that."
```

Use LangGraph thread state/checkpointing for this. A compact rolling summary may later
replace very old turns so the model is not sent an ever-growing transcript.

### 8.2 Semantic long-term memory

Purpose:

- stable preferences
- useful recurring relationships
- preferred writing style
- scheduling preferences
- facts explicitly worth remembering

Examples:

```text
preferred_email_tone = "concise, friendly, professional"
meeting_preference = "avoid meetings before 10:00"
David Kim = frequent work contact
```

Start with a structured local store (SQLite is sufficient). Each memory should eventually
carry metadata such as source/provenance, creation/update time, confidence, and whether
it was explicitly requested by the user.

### 8.3 Episodic memory (later)

Episodic memory stores **important past events or interaction summaries**, not raw chats:

```text
"User interviewed for a compiler role at NVIDIA and later discussed the result."
"Last month the user rescheduled David's project meeting to Friday."
```

Do not implement rich episodic memory before basic structured memory is reliable. Later,
semantic retrieval can use a local embedding model (for example a Sentence Transformers /
Hugging Face model) so only relevant memories enter the LLM context.

### 8.4 External systems are sources of truth, not memories

Do not copy Gmail, Calendar, Contacts, or files wholesale into Jarvis memory. Query the
source system when freshness matters:

```text
"When did I last email David?"  → Gmail
"What time is tomorrow's meeting?" → Calendar
"What is David's current email?" → Contacts
```

Memory may store a relationship such as `David Kim = frequent work contact`, but Gmail /
Calendar / Contacts remain authoritative for current external state.

### 8.5 Memory write policy

Do **not** write every conversation into permanent memory. Long-term writes need an
explicit policy:

- always support explicit "remember this" requests
- save stable preferences only when clearly useful
- avoid persisting transient remarks
- avoid sensitive information unless intentionally supported
- attach provenance where possible
- support user correction and deletion
- detect conflicts instead of silently overwriting contradictory memories

Example conflict:

```text
old: "avoid Friday meetings"
new: "Fridays are fine now"
     ↓
mark conflict / update with provenance rather than keeping both as equally true
```

The storage and retrieval interface must remain abstract so the backend can evolve from
simple SQLite lookup to semantic/vector retrieval without rewriting Jarvis Core.

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

Only Jarvis Core has a user-facing personality. The goal is an **AI butler relationship**,
not a command bot that occasionally says "sir."

Suggested initial persona:

```text
You are Jarvis, an intelligent personal assistant.

You help the user think, communicate, organize, and interact with digital services.

PERSONALITY
- calm
- articulate
- concise
- intelligent
- observant
- occasionally dry or witty
- proactive when useful, never intrusive
- confident but never pretend certainty

HUMOR
- dry rather than goofy
- occasional, not constant
- derive humor from current context or established preferences
- never force a joke into every response
- become completely serious when the situation calls for it

BEHAVIOR
- converse naturally when no tool is needed
- retain conversational context
- use relevant memory without sounding invasive
- delegate domain work silently
- ask concise clarification questions when required
- never expose internal agent/tool mechanics unless debugging
- never claim an action succeeded until a tool confirms it
- never invent recipients, dates, commitments, or external facts
- when an external action requires approval, present the exact proposed action
- avoid repetitive robotic phrases
- vary acknowledgements and do not narrate internal reasoning
```

Do not force "sir" into every sentence. It is an optional stylistic accent, not a verbal
tic. The same applies to jokes: restraint is part of the character.

### 15.1 Relationship adaptation (later)

Jarvis may later maintain lightweight **interaction preferences**, not simulated emotions:

```python
class RelationshipPreferences(BaseModel):
    preferred_address: str | None
    humor_level: float
    formality: float
    verbosity: float
```

These settings may be explicit or carefully inferred from repeated behavior. They should
change how Jarvis communicates, not what permissions it has.

Memory-driven banter should be contextual:

```text
Calendar says 08:00 meeting
+ memory says user dislikes early meetings
→ "An eight o'clock meeting, regrettably. Your past self was optimistic."
```

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

All specialist output returns to Jarvis Core for the final user-facing wording.

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
Fast Command Gate
    ├── deterministic direct tool → result ─┐
    ├── simple conversation → direct LLM ──┤
    └── complex request → Jarvis Core ─────┤
                                           ↓
                                      response text
                                           ↓
                                         Kokoro
                                           ↓
                                        speakers
```

The Fast Command Gate exists to reduce both **latency and API cost**. Common atomic
commands should not pay for a full LLM/agent round trip when deterministic parsing is
safer and faster.

Show the transcript in development:

```text
Heard: "move David to Thursday"
```

This is both a debugging tool and an ASR safety net.

### V0.2+ voice improvements

Add in this order only after the agent is reliable:

1. benchmark a lower-latency local STT backend (for example SenseVoice via sherpa-onnx)
2. VAD
3. streaming STT
4. streaming LLM output
5. streaming TTS
6. barge-in / cancel TTS when the user speaks
7. wake word
8. always-on event loop

For streaming STT, keep the interface provider-agnostic. A cloud streaming backend such
as Deepgram may be benchmarked against local STT, but the default architecture should
not depend on it. Partial transcripts can be exposed for UI feedback; only stable/final
text should trigger consequential actions.

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

## 19. Desktop application architecture

Jarvis should ultimately ship as a **downloadable desktop application**, because the
assistant must remain available while the user is working in other applications. A
browser tab or terminal should not be required for normal use.

### 19.1 Preferred shell

Preferred starting point: **Tauri 2 + a small web frontend + Python sidecar/backend**.
Electron remains a pragmatic fallback if Python-sidecar packaging or native integration
becomes disproportionately difficult. Do not rewrite the AI backend in Rust merely to
use Tauri.

```text
Tauri / Rust
    ├── app lifecycle
    ├── tray/menu bar
    ├── global shortcuts
    ├── windows / notifications
    └── OS adapter bridge

Python
    ├── Jarvis Core / LangGraph
    ├── agents
    ├── memory
    ├── STT / TTS
    ├── LLM providers
    └── Google integrations
```

### 19.2 Desktop UX

Normal operation should require as little visible UI as possible:

```text
idle      → tray/menu-bar icon only
listening → small floating orb
thinking  → orb state change
speaking  → orb state change
approval  → compact action card
review    → optional full conversation window
```

A confirmation card for an email should show the **resolved recipient, subject, body, and
operation** with `Edit`, `Save Draft`, `Cancel`, and `Send` actions where appropriate.
The same action may be approved by voice ("go ahead") or click, but both paths must feed
the same deterministic permission state.

### 19.3 Cross-platform policy

Jarvis Core must remain platform-independent. Target Windows and macOS first; Linux may
follow. Deep OS integration belongs behind an adapter:

```python
class SystemController(Protocol):
    def open_app(self, name: str): ...
    def set_volume(self, level: int): ...
    def take_screenshot(self): ...
    def notify(self, message: str): ...
```

Platform-specific permissions, accessibility APIs, app launching, shortcuts, and screen
control must not leak into `EmailAgent`, `CalendarAgent`, or Jarvis Core.

### 19.4 Packaging strategy

During development, run the Python backend and desktop shell separately if that is easier.
Do not block agent progress on `.dmg` / `.exe` polish. Later packaging must address:

- bundling Python/runtime dependencies
- local model downloads and caching
- microphone permissions
- macOS Accessibility / Screen Recording permissions when needed
- Windows equivalents
- code signing / notarization
- auto-start (optional)
- automatic updates (later)

---

## 20. API-first action execution policy

Jarvis should **prefer stable service APIs over visual browser automation**. GUI/computer
control is a fallback for services without suitable APIs or when the user explicitly asks
to see/interact with the application.

Rule:

> **API first. Native/local tool second. Computer vision + mouse/keyboard automation last.**

Examples:

```text
Email              → Gmail API
Calendar           → Google Calendar API
Contacts           → Google People / Contacts API
Open local app     → SystemController
Volume             → SystemController
Unsupported GUI app→ ComputerAgent (later)
```

### 20.1 Email behavior

The user's wording determines the operation:

```text
"write/draft an email"  → prepare internal proposal; do not imply send
"save it as a draft"    → Gmail drafts.create
"send it"               → exact-payload approval → Gmail send
```

Normal email execution does **not** open Chrome and type into Gmail. That is slower and
fragile. Use Gmail directly, then let the desktop app show the proposal.

If the user says:

> "Draft it and open it in Gmail for me."

then Jarvis may:

```text
compose → create Gmail draft via API → receive draft/message identifier → open Gmail
```

The browser is then presentation, not the mechanism of execution.

### 20.2 Calendar behavior

Likewise, calendar operations should use the Calendar API rather than clicking through a
web calendar. Read operations may run immediately. Externally visible changes still go
through the shared permission policy.

### 20.3 ComputerAgent fallback

ComputerAgent is reserved for operations that cannot be performed reliably through an
API/native tool, for example manipulating a desktop application with no supported API.
Screen understanding and GUI automation are powerful but inherently more brittle, so they
should not be the default path for Gmail or Calendar.

---

## 21. Email integration design

The previous Gmail prototype remains useful but moves behind `EmailAgent`. Email is an
**API-first** domain: Chrome should normally remain closed.

### Contact resolution

For early local development a static contact dictionary is acceptable:

```python
CONTACTS = {
    "david": "david@example.com",
    "jisoo": "jisoo@company.com",
}
```

However, V0.1 should support a real contact provider before real sending is enabled.
Multiple matches must trigger clarification. The resolved address must be known **before**
approval.

### Gmail OAuth

Keep credentials outside source control:

```text
credentials.json
*.token.json
.env
```

Start with Gmail draft creation. Enable live sends only after evals pass.

### Email lifecycle

Distinguish authoring from persistence and sending:

```text
prepare internal draft
      ↓
revise conversationally as needed
      ↓
┌───────────────────────────────┐
│ user choice                    │
├───────────────────────────────┤
│ keep internal proposal         │
│ save as Gmail draft            │
│ send exact approved message    │
│ discard                        │
└───────────────────────────────┘
```

"Write an email" does **not** automatically mean "send an email."

### Exact approval object

Do not resolve the recipient inside the final `execute()` after approval. Required order:

```text
resolve recipient
      ↓
compose
      ↓
present exact recipient + subject + body + operation
      ↓
approve
      ↓
send/save the exact approved payload
      ↓
verify Gmail response
```

If the user asks to see the message in Gmail, create the draft via API first and then open
that draft in the browser/app where practical. Do not make browser typing the primary email
implementation.

---

## 22. Calendar integration design

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

## 23. Recommended project structure

Keep the desktop shell and Jarvis backend separated so either can be tested independently.

```text
jarvis/
│
├── claudev2.md
├── README.md
├── .env.example
├── .gitignore
│
├── backend/
│   ├── main.py
│   ├── config.py
│   │
│   ├── core/
│   │   ├── jarvis.py
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── prompts.py
│   │   ├── router.py
│   │   ├── permissions.py
│   │   ├── execution.py
│   │   └── registry.py
│   │
│   ├── agents/
│   │   ├── email/
│   │   │   ├── agent.py
│   │   │   ├── schemas.py
│   │   │   ├── prompts.py
│   │   │   └── tools.py
│   │   └── calendar/
│   │       ├── agent.py
│   │       ├── schemas.py
│   │       ├── prompts.py
│   │       └── tools.py
│   │
│   ├── integrations/
│   │   ├── google_auth.py
│   │   ├── gmail.py
│   │   ├── calendar.py
│   │   └── contacts.py
│   │
│   ├── memory/
│   │   ├── working.py
│   │   ├── semantic.py
│   │   ├── episodic.py        # later
│   │   └── retrieval.py       # embeddings later
│   │
│   ├── voice/
│   │   ├── stt.py
│   │   ├── tts.py
│   │   ├── vad.py             # later
│   │   └── wakeword.py        # later
│   │
│   ├── platform/
│   │   ├── controller.py
│   │   ├── windows.py
│   │   ├── macos.py
│   │   └── linux.py            # later
│   │
│   └── transport/
│       └── local_api.py        # IPC/WebSocket boundary to desktop shell
│
├── desktop/
│   ├── src/                    # React/Svelte UI
│   ├── src-tauri/              # Rust/Tauri shell
│   └── package.json
│
└── tests/
    ├── test_core_conversation.py
    ├── test_fast_router.py
    ├── test_routing.py
    ├── test_permissions.py
    ├── test_email_agent.py
    ├── test_calendar_agent.py
    ├── test_memory.py
    ├── test_interrupt_loops.py
    └── evals/
        ├── conversations.jsonl
        ├── email_cases.jsonl
        └── calendar_cases.jsonl
```

---

## 24. Tech stack

| Layer | Initial choice | Notes |
|---|---|---|
| Desktop shell | **Tauri 2** | preferred cross-platform tray/hotkey/window shell; Electron fallback if packaging proves materially easier |
| Desktop UI | **React or Svelte** | floating orb, approvals, settings, conversation history |
| Backend | **Python** | Jarvis Core, agents, voice, memory, integrations |
| Desktop/backend transport | **local IPC / WebSocket / localhost transport** | choose the simplest robust boundary during prototype |
| Orchestration | **LangGraph** | full Tier-2 workflows + HITL interrupts; do not use for every utterance |
| Fast router | **deterministic regex/state matcher** | Tier-0 allowlisted commands; zero LLM calls |
| LLM API | **Gemini initially; provider abstraction** | Tier-1 conversation + Tier-2 reasoning; keep OpenRouter/other providers swappable |
| Local LLM option | **Ollama-compatible backend later** | optional zero-API-cost/private fallback; benchmark latency/tool reliability |
| Schemas | **Pydantic** | specialist inputs/outputs + exact action proposals |
| STT | **faster-whisper** | local starting point |
| Low-latency STT candidate | **SenseVoice / sherpa-onnx** | benchmark later, especially on accelerated hardware |
| Streaming STT candidate | **Deepgram or equivalent** | optional cloud path; partial + final transcripts |
| Audio | **sounddevice** | push-to-talk first |
| TTS | **Kokoro** | local low-latency starting point |
| Semantic memory retrieval | **Sentence Transformers / Hugging Face later** | local embeddings when structured memory outgrows simple lookup |
| Durable memory | **SQLite initially** | working checkpoints + structured semantic memory; episodic memory later |
| Email | **Gmail API** | API-first drafts + send |
| Contacts | **Google People / Contacts provider** | recipient resolution |
| Calendar | **Google Calendar API** | API-first read/write capability |
| OS integration | **SystemController adapters** | Windows/macOS first; Linux later |

Pin dependency versions only after the first runnable integration works. LangChain and
LangGraph APIs evolve; implementation must be tested against the exact pinned versions
rather than trusting stale snippets.

---

## 25. Core graph — conceptual design

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

The top-level runtime should invoke this graph **only for Tier-2 requests**. Tier-0
atomic commands and Tier-1 simple conversational turns should avoid graph construction /
agent traversal overhead where possible. They still share the same conversation identity,
persona configuration, telemetry, and safety policy.

---

## 26. Runtime loop

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

## 27. Example scenarios the architecture must pass

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

## 28. Evaluation suite

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

## 29. Build roadmap

Build inside-out, but expose the butler experience early enough to evaluate it continuously.

### Phase 0 — project scaffold

- create backend / desktop separation
- configure environment variables and `.gitignore`
- implement `LLMProvider` abstraction
- configure Gemini as the initial hosted LLM API
- make a text-only CLI

**Exit condition:** backend starts one persistent text conversation.

### Phase 1 — Jarvis Core conversation

- persistent LangGraph thread
- Jarvis persona
- natural conversation and restrained banter
- message history / working memory
- no specialist agents yet

**Exit condition:** Jarvis can hold a multi-turn conversation without forcing every turn
into a task schema.

### Phase 2 — low-latency fast paths

- deterministic Fast Command Gate
- allowlisted Tier-0 atomic local actions
- state-aware approval/reject recognition
- Tier-1 direct LLM conversation path
- Tier-2 fallback into Jarvis Core
- latency logging for STT, routing, LLM, tools, and TTS

**Exit condition:** safe obvious commands use zero LLM calls; simple banter uses one model
call; uncertainty falls through to Tier 2.

### Phase 3 — minimal voice butler loop

- faster-whisper push-to-talk
- transcript display
- Fast Command Gate after STT
- Kokoro spoken replies
- continuous voice conversation loop

**Exit condition:** the user can speak, receive an in-character spoken response, and run
allowlisted atomic actions without typing.

### Phase 4 — development desktop shell

- Tauri prototype
- tray/menu-bar presence
- global push-to-talk shortcut
- floating listening/thinking/speaking orb
- local backend transport
- compact generic approval card
- retain CLI for debugging

**Exit condition:** Jarvis can be used while another application is focused; the terminal is
not required for ordinary interaction.

### Phase 5 — EmailAgent with mocked tools

- EmailAgent schema
- recipient validation
- draft/revision lifecycle
- distinguish prepare vs save-draft vs send
- exact visual/voice approval proposal
- fake Gmail execution
- repeated interrupts

**Exit condition:** complete email workflow works safely without Gmail or browser automation.

### Phase 6 — CalendarAgent with mocked tools

- list/search events
- event reference handling
- free-slot selection
- update/create/cancel proposals
- shared permission policy

**Exit condition:** Calendar adds no email-specific coupling to Jarvis Core.

### Phase 7 — basic structured long-term memory

- SQLite semantic memory table
- explicit `remember this`
- preference / relationship records
- provenance and timestamps
- correction / deletion path
- conflict handling

**Exit condition:** a new session can recall a deliberately stored preference without
replaying the old transcript.

### Phase 8 — multi-agent delegation

- cross-agent tasks
- Calendar → Email handoff
- active task context
- clarification between subtasks

**Exit condition:** "find a free time and email David" works end-to-end with mocks.

### Phase 9 — evals

- fast-router false-positive/false-negative suite
- conversation/personality suite
- email/calendar routing suites
- HITL suite
- hallucination and ambiguity cases
- memory correctness cases
- failure/retry/idempotency cases
- latency benchmarks by tier

**Exit condition:** no external writes until core safety/eval scenarios pass reliably.

### Phase 10 — real Google integrations in safe mode

- shared OAuth utility
- Gmail draft creation
- Contacts lookup
- Calendar reads
- Calendar proposal/reversible actions where possible
- optional "open created draft in Gmail" presentation path

Keep live email sending disabled initially.

**Exit condition:** real data can be read and reversible artifacts created safely using APIs.

### Phase 11 — live external writes

- Gmail send
- Calendar create/update/cancel
- execution IDs
- API result verification
- exact payload confirmation

**Exit condition:** all live writes require the expected authorization and report success only
after API confirmation.

### Phase 12 — semantic / episodic memory upgrade

- local embeddings (Sentence Transformers / Hugging Face candidate)
- relevance retrieval
- episodic summaries for important events
- memory importance / consolidation policy
- conflict/provenance UI

### Phase 13 — realtime voice optimization

- benchmark faster-whisper vs SenseVoice/sherpa-onnx
- optional streaming STT backend
- VAD
- streaming LLM output
- streaming TTS
- barge-in
- wake word
- measure time-to-first-audio for Tier-0/Tier-1/Tier-2 separately

**Target principle:** optimize perceived conversational latency, not only total task time.

### Phase 14 — local / zero-API-cost inference option

- Ollama-compatible local LLM adapter
- compare quality, tool calling, structured output reliability, and latency against Gemini
- allow per-tier provider selection later if measurements justify it

### Phase 15 — computer control and new capabilities

In likely order:

1. ResearchAgent
2. FilesAgent
3. proactive reminders/events
4. ComputerAgent using `SystemController` + screen understanding where required
5. deeper OS/app automation

ComputerAgent should remain the fallback for capabilities without reliable APIs, not the
primary implementation for Gmail or Calendar.

### Phase 16 — distributable desktop releases

- bundle Python/backend dependencies
- model download/cache UX
- `.exe` and `.dmg` packaging
- permission onboarding
- code signing/notarization
- optional launch-at-login
- updates later

---

## 30. Immediate next action

Do **not** start by polishing the desktop application or wiring live Gmail. Build a small
vertical slice that proves the architecture:

```text
1. scaffold backend + desktop directories
2. create JarvisState and LLMProvider abstraction
3. implement text-only Jarvis Core conversation
4. add deterministic Fast Command Gate
5. add one-call Tier-1 conversational path
6. compile persistent Tier-2 LangGraph
7. add Whisper → router → Jarvis → Kokoro voice loop
8. prototype Tauri global hotkey + orb + local backend transport
9. create EmailAgent using mocked API integration
10. implement generic permission/interrupt handling
11. make the driver support unlimited interrupt/resume cycles
12. add CalendarAgent
13. add basic structured long-term memory
14. prove one cross-agent task
15. only then connect real Google APIs
```

The first milestone is **not** "Gmail sends an email" and not "we have a pretty orb."

The first meaningful milestone is:

> **Jarvis can hold a normal spoken conversation, banter naturally, recognize when a task
> appears, delegate correctly, ask for missing information, and return to conversation
> without losing context.**

---

## 31. Non-negotiable design principles

1. **Jarvis is the only user-facing persona.**
2. **Normal conversation and banter are first-class product paths.**
3. **Do not create an agent when a tool is enough.**
4. **Do not invoke the full agent graph for an obvious allowlisted atomic command.**
5. **The Fast Command Gate fails closed: uncertainty falls through to Jarvis Core.**
6. **Prefer stable APIs over GUI automation.**
7. **ComputerAgent is a fallback for unsupported interfaces, not the Gmail/Calendar default.**
8. **Do not send external writes before deterministic permission checks.**
9. **Approval must cover the exact resolved payload.**
10. **Resolve identities and ambiguity before approval/execution.**
11. **"Write/draft" is not equivalent to "send."**
12. **Never fabricate recipients, dates, commitments, memories, or tool success.**
13. **Specialists return structured results; Jarvis decides how to speak.**
14. **All specialist/external results return through Jarvis Core.**
15. **The runtime supports repeated clarification/edit/approval loops.**
16. **Keep STT/TTS outside domain-agent logic.**
17. **Load local voice models once at startup.**
18. **Verify side effects after execution.**
19. **Persist only useful long-term memory according to an explicit policy.**
20. **External services remain sources of truth for current external state.**
21. **Keep the LLM provider replaceable.**
22. **The desktop shell is a client of Jarvis Core, not Jarvis Core itself.**
23. **Keep OS-specific behavior behind platform adapters.**
24. **Optimize time-to-first-spoken-response as a first-class UX metric.**
25. **Do not overengineer multi-agent orchestration for portfolio optics.**

---

## 32. Security and credentials

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

## 33. Deployment strategy

The primary product target is a **downloadable desktop application**, not a hosted web app.
The same Python backend must remain runnable from a CLI for development and tests.

Development stages:

```text
1. Python CLI/backend
2. development Tauri shell + local backend
3. local live desktop demo
4. packaged Windows/macOS builds
5. optional hosted/mock demo for portfolio viewing
```

Do not block core agent work on installer polish. Packaging local models, Python, native
audio libraries, OAuth, and desktop permissions is its own engineering milestone.

Portfolio demo options:

```text
1. live downloadable/local desktop demo
2. recorded end-to-end butler interaction
3. mocked web/video demonstration for reviewers who cannot install it
```

Do not embed personal OAuth refresh tokens in a public build. A service account is not a
generic replacement for ordinary personal Gmail OAuth.

---

## 34. Long-term architecture

The eventual system becomes an **event-driven desktop butler**, not only request/response.

```text
             Desktop runtime / event bus
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
      user           Calendar       Gmail
      voice           events        events
        └──────────────┼──────────────┘
                       ▼
                  JARVIS CORE
                       │
                policy / memory
                       │
              speak / notify / act
```

Future examples:

```text
"Your meeting starts in fifteen minutes."
"David replied to the rescheduling email."
"The long-running research task has completed."
```

Proactive behavior requires explicit notification policies, quiet hours, and user control.
The assistant should become more available over time, not more intrusive.

Computer control may later add screen understanding and GUI automation, but API/native
integrations remain preferred wherever available.

---

## 35. Definition of success

Jarvis V0.1 is successful when this works as one persistent **desktop voice session** while
the user is working in another application:

```text
User presses global hotkey. Orb appears.

User: "Jarvis, how's tomorrow looking?"
Jarvis: reads Calendar via API and answers naturally aloud.

User: "That's awful."
Jarvis: gives a short contextual piece of banter rather than dropping task context.

User: "Move the meeting with David to Thursday afternoon."
Jarvis: finds the event, checks availability, asks only for genuinely missing info.

User: "Three is fine."
Jarvis: shows/speaks the exact proposed calendar change and requests authorization.

User: "Do it. And write him a quick apology."
Jarvis: updates Calendar via API, verifies it, resolves David, and drafts an email.

A compact desktop card shows the exact recipient/subject/body.

User: "Make it less stiff."
Jarvis: revises it while preserving the active task.

User: "Save it as a draft."
Jarvis: creates the Gmail draft through the API and verifies success. Chrome never had to open.

User: "Actually open it in Gmail."
Jarvis: opens the already-created draft for visual review.

User: "Looks good. Send it."
Jarvis: requests/accepts exact send authorization, sends the approved message, verifies Gmail.

Jarvis: "Done. The meeting is Thursday at three, and David has been notified."

User: "You're useful sometimes."
Jarvis: returns naturally to restrained banter.
```

If this works reliably, the project has demonstrated the core thesis:

> **a persistent conversational AI butler that combines personality, memory, low-latency
> voice, multi-agent reasoning, safe API execution, and desktop presence without forcing
> the user to think in terms of applications or tools.**

---

## 36. Current project status

**Architecture:** V0.1/V2 architecture established in this document, including:

- conversational Jarvis Core + restrained butler persona
- Tier-0 / Tier-1 / Tier-2 low-latency routing
- EmailAgent + CalendarAgent domain boundaries
- centralized permission / verification model
- working + semantic + future episodic memory
- API-first action execution with ComputerAgent fallback
- cross-platform desktop-shell architecture (Tauri preferred)
- local STT/TTS with provider-swappable hosted LLM reasoning

**Reusable prior work:**

- Gmail OAuth/reference code
- email composition concepts
- LangGraph human-in-the-loop concept
- faster-whisper STT code
- Kokoro TTS code
- draft-first safety principle

**Implementation:** not yet assembled under the V2 architecture.

**Next task:** scaffold `backend/` + `desktop/`, implement the text Jarvis Core and fast
routing, then prove the minimal spoken-butler vertical slice before connecting live Google
writes.
