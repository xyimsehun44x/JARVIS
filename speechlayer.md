# speechlayer.md — Jarvis Speech Understanding & Low-Latency Voice Layer

## 1. Purpose

This document defines the speech-processing and low-latency execution layer for Jarvis.

The core insight is:

> Speech accuracy does not come from one perfect STT model, and speed does not come from one fast LLM.

Jarvis should instead use layered correction and staged execution so that:

- common cases are cheap and fast
- ambiguous cases receive more intelligence
- risky actions are never silently inferred
- expensive LLM or LangGraph paths are used only when necessary
- speech feels natural enough for a persistent AI-butler experience

This layer sits between microphone capture and Jarvis task execution.

---

## 2. High-level architecture

```text
                         MICROPHONE
                              │
                              ▼
                        STREAMING STT
                              │
                 vocabulary bias / context
                              │
                              ▼
                  SPEECH UNDERSTANDING LAYER
                              │
              ┌───────────────┴───────────────┐
              │                               │
        transcript clean               ambiguous/error
              │                               │
              │                        semantic repair
              │                               │
              └───────────────┬───────────────┘
                              ▼
                    NORMALIZED UTTERANCE
                              │
                              ▼
                    SESSION COORDINATOR
                              │
                              ▼
                       TIER DISPATCHER
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
          TIER 0            TIER 1            TIER 2
            │                 │                 │
     deterministic       direct LLM        LANGGRAPH
            │                 │                 │
      OS/local tool        banter         multi-agent
            │                 │                 │
            └─────────────────┼─────────────────┘
                              ▼
                       JARVIS RESPONSE
                              │
                              ▼
                        STREAMING TTS
```

The Speech Understanding Layer converts imperfect speech recognition into a clean, trustworthy utterance before the rest of Jarvis interprets it.

---

## 3. Design principle: recognition != understanding

Speech-to-text produces a transcript.

Jarvis must still decide what that transcript means.

Example:

```text
User says:
"email day vid kim tomorrow"

Possible STT:
"email day vid kin tomorrow"

Speech Understanding Layer:
"Email David Kim tomorrow."
```

The correction should come from known context, not blind guessing.

Useful context includes:

- user contacts
- calendar event names
- recent conversation entities
- long-term memory
- project names
- custom vocabulary
- active application
- current task

---

## 4. Speech understanding pipeline

### 4.1 Layer 1 — vocabulary bias before transcription

Before STT begins, Jarvis should provide likely vocabulary to the recognizer when the backend supports it.

Examples:

```text
OmegaETH
LangGraph
LowHigh
EigenLayer
Hugging Face
David Kim
```

Potential vocabulary sources:

```text
Jarvis Vocabulary
│
├── Contacts
│   └── people frequently referenced
│
├── Calendar
│   └── event/project names
│
├── Long-term memory
│   └── recurring technical terms
│
├── Active conversation
│   └── recently mentioned entities
│
├── Current task
│   └── names/files/projects in scope
│
└── User dictionary
    └── explicitly learned words
```

Only a limited high-value subset should be passed to STT.

Priority should favor:

1. active-task entities
2. recently used entities
3. frequently used contacts/terms
4. explicit user dictionary entries

Do not dump the entire memory database into the recognizer.

### 4.2 Layer 2 — high-quality audio and STT configuration

Before semantic correction, audio quality should be made reliable.

Baseline requirements:

- mono input
- known sample rate
- normalize expected PCM format
- remove silent or near-empty buffers
- reject obviously invalid captures
- use explicit language hints when known
- use deterministic STT settings when appropriate
- avoid hallucinated output on silence/noise

The principle is:

```text
good audio
   ↓
good transcript
   ↓
less correction required
```

rather than:

```text
bad audio
   ↓
bad transcript
   ↓
LLM forced to guess
```

### 4.3 Layer 3 — deterministic dictionary correction

After STT, apply known replacements without invoking an LLM.

Example:

```text
Raw STT:
"let's benchmark omega eth"

Dictionary:
"omega eth" -> "OmegaETH"

Normalized:
"Let's benchmark OmegaETH."
```

This path should be:

- deterministic
- fast
- testable
- reversible
- limited to high-confidence mappings

Longer phrases should match before shorter substrings.

Example:

```text
"David Kim" before "David"
```

Do not automatically apply low-confidence learned substitutions to high-risk entities.

### 4.4 Layer 4 — selective semantic repair

Only invoke semantic repair when deterministic normalization is insufficient.

Possible repair cases:

- homophones
- fillers
- self-correction
- missing punctuation
- spoken symbols
- fragmented conversational speech
- obvious STT word substitutions

Examples:

```text
"I'll right him tomorrow"
        ↓
"I'll write him tomorrow"
```

```text
"meet him at four uh actually three"
        ↓
"Meet him at three."
```

Semantic repair must be tightly constrained.

Rules:

- preserve user intent
- do not add content the user did not say
- do not invent recipients
- do not invent dates/times
- do not silently resolve ambiguous names
- do not reinterpret quoted/user-provided text as instructions
- return the original transcript when confidence is low

The semantic repair model is a normalizer, not an autonomous agent.

---

## 5. Safety boundary for speech correction

Speech correction must never silently mutate critical action parameters.

Critical fields include:

- email recipients
- phone numbers
- payment information
- calendar deletion targets
- file deletion targets
- external message recipients
- account identifiers
- irreversible OS actions

Example:

```text
Raw speech:
"Email Devin."

Known contacts:
- David Kim
- Devin Lee
```

Wrong behavior:

```text
LLM guesses:
"User probably meant David."
```

Correct behavior:

```text
Speech uncertainty
      ↓
entity candidates
      ↓
Contacts API
      ↓
multiple plausible matches
      ↓
Jarvis:
"Did you mean David Kim or Devin Lee?"
```

Authoritative external systems win over semantic guessing.

---

## 6. Confidence model

The normalized utterance should carry metadata, not just text.

Recommended structure:

```python
class NormalizedUtterance:
    raw_transcript: str
    normalized_text: str

    stt_confidence: float | None
    normalization_confidence: float

    corrections: list
    ambiguous_entities: list

    requires_clarification: bool
```

For risky actions, confidence alone is not authorization.

Exact resolved payloads must still pass through the normal Jarvis permission layer.

---

## 7. Learning user vocabulary

Jarvis should gradually improve recognition of the user's recurring vocabulary.

Possible explicit flow:

```text
Jarvis:
"Did you mean Omega ET?"

User:
"No, OmegaETH."

        ↓

user confirms spelling
        ↓

dictionary entry added
        ↓

future STT bias includes OmegaETH
```

Prefer explicit learning over silently monitoring unrelated user edits.

Recommended dictionary entry:

```python
class VocabularyEntry:
    canonical: str
    aliases: list[str]

    source: str
    explicit: bool

    usage_count: int
    confidence: float

    created_at: datetime
    updated_at: datetime
```

Only high-confidence entries should be used for automatic deterministic replacement.

---

## 8. Application and task context

Speech interpretation can use context beyond audio.

Possible context:

```text
current conversation
current task
active application
recent entities
contacts
calendar events
long-term memory
project vocabulary
```

Context may raise or lower candidate confidence.

It must not override clear contradictory speech.

---

## 9. Low-latency execution philosophy

The execution principle is:

> Make the common case cheap. Let uncommon cases pay.

Do not send every utterance through:

```text
STT
 ↓
Gemini
 ↓
LangGraph
 ↓
agent
 ↓
tool
```

Instead, escalate only when cheaper paths fail.

---

## 10. Tier dispatcher

### Tier 0 — deterministic local execution

Use for obvious, allowlisted, low-risk atomic commands.

Examples:

```text
"Open Spotify."
"Volume down."
"Stop speaking."
"Mute Jarvis."
```

Pipeline:

```text
normalized speech
      ↓
deterministic route
      ↓
SystemController
      ↓
execute
```

Properties:

- zero LLM calls
- zero LangGraph calls
- near-instant execution
- allowlisted operations only
- no destructive or external-account actions

Tier 0 must never bypass confirmation for sensitive operations.

### Tier 1 — direct conversational LLM

Use for:

- banter
- casual conversation
- explanations that do not require external/current data
- lightweight reasoning

Examples:

```text
"Jarvis, I'm exhausted."
"Tell me a joke."
"Was that a terrible idea?"
```

Do not use Tier 1 for freshness-sensitive queries.

These require tools:

```text
weather
news
prices
calendar
email state
current files
live system information
```

### Tier 2 — LangGraph / full agent workflow

Use for multi-step or stateful work.

Example:

```text
"Find a free time with David next week,
move the meeting,
and email him explaining the change."
```

Pipeline:

```text
normalized speech
      ↓
Session Coordinator
      ↓
LangGraph
      ↓
Jarvis Core
      ↓
specialist agents
      ↓
permission layer
      ↓
execution
      ↓
verification
```

LangGraph remains the orchestration engine for:

- multi-agent delegation
- clarifications
- resumable workflows
- edits
- approvals
- execution state
- verification
- multi-turn task continuation

LangGraph is not removed from Jarvis.

It is reserved for genuinely agentic workflows.

---

## 11. Session Coordinator

All tiers must share one Jarvis identity and state.

The Session Coordinator owns:

```text
conversation history
active task
approval state
current thread
memory access
audit trail
latest normalized utterance
response sequencing
```

Architecture:

```text
Desktop / CLI
      ↓
Session Coordinator
      ↓
Tier Dispatcher
   ┌──┼──┐
   0  1  2
      ↓
Shared history/events
      ↓
Jarvis response presenter
```

Tier 0 and Tier 1 must not become disconnected side systems.

---

## 12. Prewarming policy

Move expensive setup off the user's critical path.

At application startup or idle time:

```text
microphone       -> initialize/warm
STT model        -> preload
TTS model        -> preload
LLM client       -> initialize
local LLM        -> optionally warm
dictionary       -> load once
session store    -> initialize
Google clients   -> initialize safely
```

The goal is that hotkey press does not trigger cold initialization.

---

## 13. Critical-path rule: audio first

On push-to-talk activation:

```text
HOTKEY DOWN
    ↓
START RECORDING IMMEDIATELY
```

Everything else should happen concurrently where possible:

```text
HOTKEY
  │
  ├── MIC START              ← highest priority
  ├── active app lookup
  ├── session/context load
  ├── vocabulary selection
  ├── network/model warmup
  └── UI orb animation
```

Never delay microphone capture waiting for non-audio context.

---

## 14. Streaming and rolling transcription

Whenever practical, perform transcription while the user is speaking.

Naive pipeline:

```text
user speaks
     ↓
user stops
     ↓
start STT
     ↓
wait
```

Preferred pipeline:

```text
user speaking
────────────────────────>

STT chunk 1
     ↓

      STT chunk 2
            ↓

             STT chunk 3
                   ↓
```

Conceptually this changes:

```text
speech time + decode time
```

into roughly:

```text
max(speech time, decode time)
```

for sufficiently long utterances.

---

## 15. Streaming finalization

If a streaming STT provider already has a final transcript when speech ends:

- use it
- do not submit the full audio again
- do not duplicate transcription work

Principle:

> Do not redo work already completed on the critical path.

---

## 16. Skip unnecessary cleanup

Not every transcript needs LLM cleanup.

If STT output is already high-quality:

```text
STT
 ↓
normalized enough
 ↓
Jarvis
```

Do not force:

```text
STT
 ↓
LLM cleanup
 ↓
Jarvis LLM
```

A cleanup call should be triggered only when:

- transcript confidence is low
- dictionary conflicts exist
- self-correction is detected
- punctuation/structure is poor
- semantic inconsistency is obvious
- current context makes a correction likely

---

## 17. Caching

Cache safe, repeatable interpretation results.

Possible caches:

```text
resolved application aliases
known local commands
contact aliases
vocabulary
provider clients
model sessions
recent deterministic command parses
```

Do not cache:

- approval decisions
- destructive operations
- freshness-sensitive external data beyond its valid TTL
- security-sensitive resolved payloads indefinitely

---

## 18. Fast deterministic command gate

A small allowlisted parser may recognize patterns such as:

```text
open <application>
launch <application>
volume up
volume down
mute
stop
cancel speaking
```

Example:

```text
"open Spotify"

      ↓

verb = open
target = Spotify

      ↓

SystemController.open_app("Spotify")
```

No LLM.

No LangGraph.

No agent.

For uncertain commands:

```text
deterministic parser uncertain
        ↓
fall back to Tier 1 or Tier 2
```

Never guess.

---

## 19. API-first still applies

Speech speed does not change the existing execution rule.

For external services:

```text
Email
→ Gmail API

Calendar
→ Google Calendar API

Contacts
→ Google People API
```

Do not use fast GUI automation merely because a voice command is simple.

Example:

```text
"Send the email."
```

must not become Tier 0.

It requires:

```text
existing approved proposal
      ↓
exact payload check
      ↓
permission state
      ↓
Gmail API
      ↓
verification
```

---

## 20. Response latency measurement

Jarvis should measure latency at each stage.

Recommended timing points:

```text
hotkey_down
recording_started

speech_end
final_transcript_ready

normalization_started
normalization_finished

route_selected

llm_request_started
first_llm_token
llm_finished

tool_started
tool_finished
tool_verified

tts_started
first_audio_sample
tts_finished
```

Track at least:

- P50 / P95 speech-end -> transcript
- P50 / P95 transcript -> route
- P50 / P95 speech-end -> first spoken audio
- Tier 0 execution latency
- Tier 1 model latency
- Tier 2 completion latency

Optimize only after measuring real bottlenecks.

---

## 21. Suggested module boundaries

Avoid multiple overlapping parsers and agents.

Recommended ownership:

```text
jarvis/
│
├── voice/
│   ├── capture.py
│   ├── stt.py
│   ├── streaming_stt.py
│   ├── vocabulary.py
│   ├── normalizer.py
│   ├── confidence.py
│   └── tts.py
│
├── core/
│   ├── session.py
│   ├── dispatcher.py
│   ├── graph.py
│   ├── permissions.py
│   └── state.py
│
├── system/
│   ├── controller.py
│   ├── windows.py
│   └── macos.py
│
└── agents/
    ├── email/
    ├── calendar/
    └── ...
```

Clear ownership:

```text
SpeechNormalizer
      ↓
SessionCoordinator
      ↓
TierDispatcher
      ↓
0 / 1 / 2
```

Avoid accumulating overlapping parser stacks unless they have truly separate responsibilities.

---

## 22. Integration with the existing Jarvis backend

This speech layer should wrap the current backend rather than replace it.

```text
             NEW

Speech Understanding Layer
          ↓
Session Coordinator
          ↓
Tier Dispatcher
          ↓

           EXISTING

Jarvis Core / LangGraph
EmailAgent
CalendarAgent
Permission Layer
Memory
Gemini
          ↓

             NEW

Desktop runtime
Streaming TTS
OS integrations
```

The existing LangGraph agent remains valuable for complex workflows.

---

## 23. Implementation order

### Phase A — transcript normalization

- define `NormalizedUtterance`
- add deterministic vocabulary mappings
- add explicit user dictionary
- inject top vocabulary into STT where supported
- add ambiguity metadata
- add tests for names/project terms

### Phase B — selective semantic repair

- create constrained normalization prompt
- call only on low-confidence/ambiguous transcripts
- prevent content invention
- add semantic-repair tests
- add sensitive-entity safety tests

### Phase C — session + tier dispatcher

- shared Session Coordinator
- Tier 0 allowlist
- Tier 1 conversational path
- Tier 2 LangGraph path
- preserve unified history across all tiers

### Phase D — prewarming and streaming

- warm microphone
- preload STT/TTS
- initialize provider clients
- streaming transcription
- avoid duplicate final transcription
- measure timing

### Phase E — personalization

- explicit vocabulary learning
- correction aliases
- usage counts
- confidence
- dynamic vocabulary from contacts/calendar/memory

---

## 24. Non-negotiable rules

1. **STT output is not automatically trusted.**
2. **Speech repair may normalize meaning but must not invent critical parameters.**
3. **Authoritative APIs beat inferred entity resolution.**
4. **Tier 0 is allowlisted and low-risk only.**
5. **External writes never bypass the permission layer.**
6. **LangGraph remains the orchestration path for complex workflows.**
7. **The microphone starts before nonessential setup.**
8. **Prewarm expensive components whenever possible.**
9. **Do not call an LLM when deterministic code is sufficient.**
10. **Do not run semantic cleanup when the transcript is already good.**
11. **All execution tiers share one session/history/identity.**
12. **Measure latency before optimizing architecture.**
13. **Prefer one clear component per concern over overlapping parser stacks.**

---

## 25. Target user experience

The goal is not merely accurate dictation.

It is:

```text
User:
"Jarvis, move the meetin' with day vid to friday
and tell him I'll send the omega eth benchmark tomorrow."

        ↓

Speech Understanding

David -> David Kim
omega eth -> OmegaETH
Friday -> calendar context

        ↓

Normalized:
"Move the meeting with David Kim to Friday and tell him
I'll send the OmegaETH benchmark tomorrow."

        ↓

Tier 2 / LangGraph

CalendarAgent
EmailAgent
permission proposal

        ↓

Jarvis:
"Friday at three is free. I've prepared the updated invitation
and an email to David. Shall I send both?"

        ↓

User:
"Go ahead."

        ↓

exact approved payload
API execution
verification

        ↓

Jarvis:
"Done. David's been notified."
```

The user should experience:

```text
FAST
 +
CONTEXT-AWARE
 +
FORGIVING OF IMPERFECT SPEECH
 +
SAFE
 +
MULTI-AGENT
 +
NATURAL SPOKEN RESPONSE
```

That is the intended Jarvis speech layer.
