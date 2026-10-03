# Jarvis V0.1

Jarvis is a persistent, conversational assistant with specialist email, calendar, and
weather capabilities. The implementation follows [claudev2.5.md](claudev2.5.md): Jarvis is the only
user-facing persona, normal conversation is a first-class path, specialists exchange
typed data, and externally visible actions require approval of the exact payload.

The project supports two modes. `mock` is the safe default for tests and offline
development. `google` uses Gemini 3.8 Flash by default, Google People, Gmail, Google
Calendar, and durable SQLite checkpoints. OpenAI remains an optional model provider.
Live email sending and calendar changes have independent feature locks in addition to
Jarvis's exact-payload approvals. Current weather is read from Open-Meteo in real-service
mode and always includes its source and retrieval time; mock mode uses deterministic
local forecast data.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py --text
```

## Configure real services

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.
   `low` reasoning is used for voice responsiveness, and routing plus an ordinary
   conversational reply share one model request. Change
   `JARVIS_GEMINI_THINKING_LEVEL` if a task needs deeper reasoning.
2. In a Google Cloud project, enable Gmail API, Google Calendar API, and People API.
3. Configure the OAuth consent screen, create a **Desktop app** OAuth client, and save
   the downloaded file as `credentials.json` in this directory.
4. Check readiness and authorize:

```powershell
python main.py --check
python main.py --setup-google
python main.py --text
```

Keep `JARVIS_ALLOW_EMAIL_SEND=false` and
`JARVIS_ALLOW_CALENDAR_WRITES=false` while testing real reads and Gmail drafts. When
you deliberately enable either flag, restart Jarvis and rerun Google setup if the
required OAuth scope changed.

If Google expires or revokes the saved refresh token, setup treats that token as absent
and opens a fresh interactive authorization instead of failing with `invalid_grant`.
The Gemini API key and Google Workspace OAuth token are separate credentials: success in
AI Studio does not validate Gmail, Calendar, or Contacts access.

Set `JARVIS_DEFAULT_LOCATION=Seoul, South Korea` (or another city and country) to
answer location-free weather questions directly. Without it, Jarvis asks for a
location once. `JARVIS_WEATHER_TIMEOUT_SECONDS` controls the read timeout and defaults
to five seconds. Open-Meteo weather data is used under its published
[API terms and attribution](https://open-meteo.com/en/terms).

Try one continuous session:

```text
What's tomorrow looking like?
Move the dinner to Friday
five
yes
Find when I'm free next week and email David Kim a couple of options
send it
Thanks
```

The CLI automatically resumes any clarification or approval interrupt. To exercise
the API directly:

```python
from jarvis import Jarvis, SessionCoordinator

jarvis = Jarvis()
session = SessionCoordinator(jarvis)
result = session.turn("Email David a quick update", thread_id="demo")
while result.needs_input:
    print(result.prompt)
    result = session.turn(input("> "), thread_id="demo")
print(result.response)
```

Run tests with:

```powershell
python -m pytest
```

Run the declarative conversation, email, and calendar evaluations with:

```powershell
python -m pytest tests/test_evals.py -vv
```

## Desktop IPC foundation

Milestone 3 begins with a private, versioned JSONL protocol over the stdin/stdout pipes
of a desktop-owned Python child process. Start that backend mode with:

```powershell
python main.py --ipc-stdio
```

The backend writes one protocol-version-1 `ready` event, then accepts `health`, `turn`,
`voice.start`, `voice.stop`, `voice.cancel`, and `shutdown` requests. Every request needs
a unique `request_id`; text and voice turns also carry their persistent `thread_id`:

```json
{"protocol_version":1,"request_id":"turn-1","method":"turn","params":{"text":"Hello, Jarvis","thread_id":"desktop-main"}}
```

Tier-1 text may arrive first as `assistant.delta` events. Desktop push-to-talk additionally
emits `voice.state` and `voice.transcript` events while using the same local Whisper,
speech-normalization, session-routing, vocabulary, and Kokoro playback path as the CLI.
The terminal response contains the exact assembled response plus clarification, approval,
trace, and execution-tier fields needed by the desktop UI. Malformed requests receive a
structured error without stopping the backend. This mode reserves stdout for protocol
messages; desktop process logs and crash diagnostics must use stderr. Stdio is
intentionally the first transport so the backend is reachable only by the process that
launched it, with no listening network port.

For streamed model turns, Jarvis reconciles an exact missing suffix from Gemini's final
completed model-output payload when available. It never guesses a continuation or merges
divergent output. A voice response that still has no complete terminal sentence is
discarded and replaced with a spoken retry message instead of being written into the
shared conversation thread.

The first Windows desktop scaffold is in [`desktop/`](desktop/README.md). After installing
the Tauri prerequisites, run it locally with:

```powershell
cd desktop
npm.cmd install
npm.cmd run tauri dev
```

It launches and owns the Python backend, displays readiness/crash state, streams Tier-1
text into a persistent transcript, and renders exact approval payloads with approve/cancel
controls. Click the microphone once to begin recording and again to stop, transcribe, and
send. The desktop backend warms Whisper and Kokoro during startup and does not advertise
readiness until both are loaded, so a ready microphone begins recording immediately. On a
first-ever launch, model downloads can keep the app in `Starting backend` longer. Jarvis
speaks the response through the existing Kokoro pipeline. Closing the window keeps the
backend alive in the Windows notification area; the tray menu restores or hides the
existing window and provides an explicit graceful Quit action. `Ctrl+Alt+J` globally
shows or hides that same window, and `JARVIS_DESKTOP_HOTKEY` can override the shortcut in
the launch environment when no saved preference exists. The gear opens a local
settings/diagnostics panel with backend, model, voice, and safety-lock status. Shortcut
changes are validated immediately and persisted without credentials or conversation
content. Packaging the Python process as a distributable sidecar remains a production
hardening task.

## Safety model

- Calendar reads and contact lookup run without confirmation.
- Draft saving is reversible and confirmation is configurable.
- Email sending and calendar create/update require confirmation.
- Calendar cancellation is destructive and requires explicit confirmation.
- The approved proposal is hashed and recorded before execution.
- In SQLite mode, a repeated identical approved payload is not executed again after a
  process restart. Interrupted or ambiguous external writes become `uncertain` and are
  not retried until reconciliation explicitly verifies the result or declares a retry safe.
- Success is reported only when the provider returns a verified result.
- Long-term memory writes require an explicit user request in V0.1.

## Structured long-term memory

Long-term memory changes are deterministic and require explicit language. Ordinary
conversation never creates a memory. Supported examples include:

```text
Remember that my meeting preference is avoid Mondays.
What do you remember about my meeting preference?
Correct what you remember about my meeting preference to prefer Tuesdays.
Forget what you remember about my meeting preference.
```

Memories carry stable IDs, kind, provenance, confidence, sensitivity, lifecycle status,
timestamps, and supersession links. Corrections preserve the previous record; forgetting
soft-deletes the active record so the change remains auditable. Only relevant active,
non-sensitive memories may be supplied to ordinary conversation, as untrusted data rather
than instructions. Sensitive memories require explicit recall, and credentials, access
tokens, payment-card data, and private keys are refused entirely.

The desktop settings panel exposes the same lifecycle through private typed IPC. It lists
active records and audit history, supports inline correction with exact old/new values,
requires deliberate confirmation before forgetting, and restores only eligible forgotten
records. Credential-like legacy rows are never returned to the desktop, and memory
controls cannot authorize email, calendar, or operating-system actions.

Credentials, tokens, local databases, and `.env` are ignored by Git. Google mode is
lazy: starting Jarvis does not authorize an account, and API access occurs only when a
specialist needs it.

## Project map

```text
jarvis/core/          LangGraph, state, routing, permissions, execution
jarvis/agents/        Typed email and calendar specialists
jarvis/integrations/  Mock and OAuth-backed Google providers
jarvis/ipc/           Versioned JSONL protocol and desktop-owned stdio server
jarvis/memory/        Explicit long-term policy; checkpoints for short-term context
jarvis/voice/         Lazy push-to-talk STT and Kokoro TTS adapters
desktop/              Windows-first Tauri client and Python child-process lifecycle
tests/                Behavioral and safety coverage
```

The CLI enters Jarvis through `SessionCoordinator`. Ordinary conversation takes the
one-call Tier-1 path while sharing the same persistent thread as Tier-2 email/calendar
work. Explicit approval or rejection of a locally pending action is recognized by the
deterministic Tier-0 gate. Fresh-data questions and uncertain intent fail closed into
Tier 2 rather than being answered by the conversational fast path. Weather is the
currently supported live fresh-data domain. Requests for unsupported live news, prices,
scores, traffic, or air quality get an explicit unavailable response instead of a
model-generated guess.

Voice remains optional because its local model packages are large. Install with
`python -m pip install -e ".[voice]"`, then run `python main.py --voice`. The same
graph, permissions, OAuth providers, and persistent thread are used in either mode.
The first voice launch downloads the selected faster-whisper and Kokoro model assets;
subsequent launches reuse the local model cache.
Those public assets can be downloaded anonymously. An optional read-only `HF_TOKEN` in
`.env` authenticates Hub requests for higher rate limits, but it is not required once
the models load successfully.

The default voice profile uses `small.en`, beam-5 Whisper decoding, voice-activity
filtering, bounded input-level normalization, low Gemini thinking for ordinary
spoken conversation, short spoken answers, and no separate model-routing request.
Structured tool work retains its existing reasoning policy. A conservative speech
normalizer repairs known non-entity homophones before routing and prints an `Understood`
line whenever it changes the raw transcript. Contact names and other critical entities
are never silently rewritten. STT returns content-free audio and segment-quality
metadata; invalid captures and low-confidence actions are repeated before routing. The
action threshold is configurable with `JARVIS_STT_ACTION_CONFIDENCE_THRESHOLD`. Add only
stable assistant/domain terms to
`JARVIS_STT_HOTWORDS` or `JARVIS_STT_INITIAL_PROMPT`; strongly biasing a contact name can
reduce accuracy for ordinary speech.

## Personal speech vocabulary

Jarvis keeps explicit vocabulary in the gitignored file configured by
`JARVIS_VOCABULARY_PATH`. Add canonical project or product terms with one or more aliases:

```powershell
python main.py --vocabulary-add "OmegaETH" --vocabulary-alias "omega eth"
python main.py --vocabulary-list
python main.py --vocabulary-remove "OmegaETH"
```

Automatic entries above 0.8 confidence are applied longest-alias-first after STT and may
contribute at most 15 canonical terms to Whisper's weak initial prompt. Recent thread
context and usage determine ordering. Entries never enter strong hotwords. Mark a name or
other critical identifier with `--vocabulary-sensitive`; protected entries are stored for
explicit management but are not automatically substituted or injected into STT. Duplicate
or ambiguous aliases are rejected.

## Latency diagnostics

Enable structured timing output for one run with:

```powershell
python main.py --voice --latency
```

For a repeatable before/after benchmark with a sanitized JSON report, follow
[benchmarks/VOICE_BASELINE.md](benchmarks/VOICE_BASELINE.md).

Or set `JARVIS_LATENCY_LOGGING=true` to keep it enabled. Jarvis prints per-interaction
timings and P50/P95 session statistics for available stages such as audio capture, STT,
routing, model first token/response, first speakable chunk, contact/Google tools, TTS
synthesis/playback, time to first TTS audio, and total TTS playback. Tier-1 Gemini text
is streamed into a
sentence-buffered Kokoro queue so synthesis of the next chunk can overlap current
playback. Timing events contain stage names, durations, and trace/thread IDs; they do not
contain transcripts, prompts, email contents, or credentials. STT events may include
content-free duration, level, gain, clipping, segment score, no-speech score, and quality
classification metadata.
