# Jarvis Project Handoff

Last updated: 2026-09-11

This document is the continuation point for a new development chat. It records what
has actually been implemented, what has been verified, what remains incomplete, and
the recommended milestone order.

## Read this first

- The project is no longer just a mock. The runtime supports real Gemini, Google
  OAuth, Gmail, Google Contacts, and Google Calendar integrations.
- Automated tests intentionally use mocks and fakes so they cannot send email or
  alter a real calendar.
- The working tree currently contains uncommitted project changes. Preserve them.
  Do not reset, check out, or replace files merely because they differ from the last
  commit.
- Never commit `.env`, `credentials.json`, `google.token.json`, API keys, OAuth
  secrets, or other personal credentials.
- Keep live Gmail sending and Calendar writes disabled until the corresponding flows
  have been tested safely with the user's account.

## Canonical design documents

- `claudev2.5.md` is the current product and architecture target used for the ongoing
  migration.
- `CLAUDE.md` is an older architecture document and should not be treated as a more
  current implementation record than this handoff.
- `claudev2.md` is also historical. Its title is accidentally repeated at the top of
  `claudev2.5.md`; the filename, not that heading, identifies the newer document.
- `README.md` contains current setup, safety, testing, and runtime instructions.
- This file is the current implementation checkpoint and roadmap.

The existing Python package is the backend. Do not move `jarvis/` into a new
`backend/` directory just to match a conceptual diagram. When desktop work begins,
add a sibling `desktop/` application and keep the Python CLI operational.

## Current product status

Jarvis is a Python 3.11+ voice/text assistant with a real provider path and a safe
mock/test path.

Implemented runtime capabilities:

- Gemini through Google's native SDK, currently configured for
  `gemini-3.8-flash`, with an OpenAI-compatible fallback path.
- Google OAuth setup and token persistence.
- Gmail draft creation and sending, with separate semantics and safety gates.
- Google Contacts lookup for email recipients.
- Google Calendar reads, event creation, updates, and cancellation.
- Verified current/today/tomorrow weather through a read-only Open-Meteo provider,
  with deterministic mock data for tests.
- Text mode and push-to-talk voice mode.
- Faster-Whisper speech-to-text and Kokoro text-to-speech.
- Persistent LangGraph checkpoints backed by SQLite.
- Persistent execution records and exact task artifacts backed by SQLite.
- Direct conversation, specialist routing, clarification, proposals, approval,
  execution, and post-write verification.
- Latency tracing and per-stage/session percentile summaries.

This is not yet the complete desktop Jarvis described in `claudev2.5.md`. The current
interface is a CLI, voice interaction is blocking push-to-talk, memory is only partly
wired into behavior, and several planned tool domains do not yet exist.

## Architecture as implemented

The effective runtime flow is:

```text
text or push-to-talk audio
        |
        v
SessionCoordinator
        |
        +-- Tier 0: exact approval/rejection of a pending local task
        |
        +-- Tier 1: ordinary conversation, one direct model call
        |
        `-- Tier 2: action/fresh-data routing through Jarvis Core
                         |
                         +-- email specialist
                         +-- calendar specialist
                         +-- weather read tool
                         +-- explicit unsupported-fresh-data response
                         `-- conversation fallback
                                  |
                                  v
                    permission -> execute -> verify
```

Important implementation details:

- `SessionCoordinator` selects an execution tier before invoking the full workflow.
- Tier 1 conversation bypasses unnecessary orchestration but is persisted into the
  same LangGraph thread, so context survives a SQLite-backed restart.
- Tier 0 is currently for approving or rejecting an already pending proposal. It is
  not yet a general local operating-system command tier.
- Tier 2 handles explicit or ambiguous actions, email/calendar work, verified weather,
  and questions that appear to require fresh external data.
- `jarvis/core/graph.py` currently contains one top-level `jarvis_core` graph node.
  The email and calendar specialists are real classes called by Jarvis Core; they are
  not separate graph nodes yet. A graph rewrite is not required for the next
  milestone.
- Exact generated task artifacts are stored durably. After an interrupt or restart,
  approval resumes the same proposal instead of asking the model to recreate it.

## Completed stabilization work

### 1. Calendar cancellation correctness

- Fixed mock calendar cancellation so it belongs to `MockCalendarProvider`.
- Removed an accidental duplicate method in `GoogleCalendarProvider` that overrode
  the real API implementation and referenced mock-only state.
- Added coverage for approved mock cancellation and Google delete/404 verification.

### 2. Correct email intent semantics

Email operations are now explicit:

- `prepare`: create an internal proposal without mutating Gmail.
- `save_draft`: create a real Gmail draft.
- `send`: send a message only after the exact proposal is approved.

Consequences:

- "Write/draft an email" prepares content internally.
- "Save it as a draft" requests a Gmail draft.
- Explicit "send" language, or a direct command such as "email June", requests a
  send proposal.
- A prepared proposal can be revised, saved as a draft, or sent.
- Sending still requires approval of the exact recipient, subject, and body.
- Fail-closed intent tests cover ambiguous wording.

### 3. Crash-safe side-effect execution

Execution records use these states:

- `executing`
- `verified`
- `failed`
- `uncertain`

The runtime does not automatically retry a stale `executing` or `uncertain` action.
It first requires reconciliation. A recovery hook can classify the previous attempt
as verified, failed, safe to retry, or still uncertain. Only an explicit `retry`
decision permits a repeated write.

Provider code distinguishes failures known to occur before an API call from ambiguous
exceptions that may have happened after a remote write. Tests simulate a hard crash,
restart from SQLite, duplicate blocking, reconciliation, and an explicit safe retry.

Current limitation: generic recovery plumbing exists, but provider-specific automatic
reconciliation is not complete. An uncertain live action may still require a manual
check before retrying.

### 4. Executable conversation and safety evaluations

`tests/test_evals.py` executes JSONL scenarios from `tests/evals/` rather than treating
them as prose fixtures. The current suite includes 14 eval scenarios covering:

- ordinary conversation and routing,
- email intent and confirmation safety,
- calendar proposals and changes,
- follow-up turns and interrupts,
- proposal operation and exact response expectations,
- mock write counts and verified execution outcomes.

See `tests/evals/README.md` for the case format.

### 5. Latency instrumentation

`jarvis/core/latency.py` provides trace-aware timing, P50/P95 statistics, and summaries
without logging the user's message or audio content. Instrumented stages include:

- audio capture,
- STT model loading and transcription,
- tier dispatch and routing,
- LLM classification, response, email drafting, and email revision,
- Contacts, Calendar, and Gmail tool operations,
- total turn time,
- TTS loading, first audio, and total synthesis,
- speech-end to first response audio.

Enable it with either `JARVIS_LATENCY_LOGGING=true` or:

```powershell
python main.py --voice --latency
```

The CLI prints per-turn timings and session percentiles. The instrumentation is done;
the remaining task is to collect a representative benchmark on the user's hardware
and optimize the measured bottleneck.

### 6. Session coordinator and execution tiers

`jarvis/core/session.py` adds:

- `ExecutionTier`
- `TierDecision`
- `TierDispatcher`
- `SessionCoordinator`

The CLI now goes through this coordinator. Ordinary conversation takes a faster
single-model-call path while action requests and fresh-data questions remain on the
safer routed path. Restored unknown pending tasks fail toward Tier 2. `TurnResult`
records a trace ID, selected tier, and tier reason for diagnostics.

### 7. Durable exact task artifacts

`jarvis/core/artifacts.py` stores task artifacts in memory or SQLite. It currently
caches email drafts/revisions and related availability/calendar artifacts across
LangGraph interrupt replay and process restarts.

This closes an important safety gap: the content approved by the user is the content
that proceeds to execution. Tier 0 approval does not silently redraft an email with a
new model call.

### 8. Voice contact-name correction

STT configuration now supports hotwords and low-beam fast recognition. Conversation
normalization handles common correction phrases and spelled names such as:

- "I meant J-O-O"
- "I meant say J-O-O"
- repeated or corrective fragments after a failed recipient lookup

This improves the earlier "Joo" contact example. It is still constrained by the
push-to-talk transcription model and is not a substitute for streaming contextual
ASR.

## Verification status

Latest recorded full test result at this checkpoint:

```text
115 passed
```

The suite includes unit, provider, persistence, session, latency, and JSONL evaluation
tests. Recent automated verification used mocks/fakes and did not perform live weather
requests, Gmail sends, or Calendar writes.

Useful commands:

```powershell
python -m pytest -q -p no:cacheprovider
python -m pytest tests/test_evals.py -vv
python main.py --check
python main.py --text
python main.py --voice --latency
```

Google OAuth setup, when needed:

```powershell
python main.py --setup-google
```

## Configuration and credential safety

Use `.env.example` as the template and keep personal values only in `.env`. The
important current variables are:

```dotenv
JARVIS_MODE=google
JARVIS_LLM_PROVIDER=gemini
GEMINI_API_KEY=
JARVIS_GEMINI_MODEL=gemini-3.8-flash
JARVIS_GEMINI_THINKING_LEVEL=low
JARVIS_STT_MODEL=small.en
JARVIS_STT_BEAM_SIZE=5
JARVIS_STT_INITIAL_PROMPT=Jarvis personal assistant; email, calendar, meeting, weather, forecast, contacts, Seoul, Busan
JARVIS_STT_VAD_FILTER=true
JARVIS_STT_HOTWORDS=Jarvis
JARVIS_STT_ACTION_CONFIDENCE_THRESHOLD=0.35
JARVIS_STT_VOCABULARY_LIMIT=15
JARVIS_VOCABULARY_PATH=jarvis.vocabulary.json
JARVIS_LATENCY_LOGGING=false
JARVIS_TIMEZONE=Asia/Seoul
JARVIS_DEFAULT_LOCATION=
JARVIS_WEATHER_TIMEOUT_SECONDS=5
JARVIS_PERSISTENCE=sqlite
JARVIS_DATABASE_PATH=jarvis.db
JARVIS_GOOGLE_CREDENTIALS=credentials.json
JARVIS_GOOGLE_TOKEN=google.token.json
JARVIS_ENABLE_GMAIL=true
JARVIS_ENABLE_CALENDAR=true
JARVIS_ENABLE_CONTACTS=true
JARVIS_ALLOW_EMAIL_SEND=false
JARVIS_ALLOW_CALENDAR_WRITES=false
```

Do not paste the real Gemini key, OAuth client secret, or OAuth token into a chat,
test fixture, log, or documentation file.

## Known gaps and limitations

### Other fresh information and location

Weather is the first verified live-data domain. News, market prices, scores, traffic,
air quality, and other unsupported fresh-data requests fail closed with an explicit
unavailable response rather than being passed to the language model as current facts.

There is no location API or automatic geolocation. Weather uses an explicit location,
the configured `JARVIS_DEFAULT_LOCATION`, or one concise clarification. Device or IP
geolocation can be considered later as an opt-in feature.

### Voice responsiveness

Voice capture and transcription are still push-to-talk and sequential. Tier-1 response
generation and speech are now pipelined:

```text
record -> transcribe -> stream model -> sentence buffer -> synthesize || play
```

It has VAD, input-level normalization, invalid-capture rejection, confidence-aware action
gating, sentence-sized speech chunks, early Gemini text streaming, and separate Kokoro
synthesis/playback queues. Tier 2 intentionally retains its complete-response path. Voice
mode does not yet have automatic endpoint detection, streaming transcription, barge-in,
or a cancellable audio controller.

### Memory

Short-term/checkpoint persistence works. A long-term memory storage scaffold exists,
but preferences and facts are not yet consistently recalled and injected into normal
conversation. Remember/recall/forget/correct semantics and user controls remain to be
implemented.

### Desktop presence

There is no Tauri desktop application, tray icon, global hotkey, orb, approval card,
settings UI, or sidecar lifecycle management. The current product surface is the
Python CLI.

### Local computer actions

There is no general Windows control adapter. Volume, allowlisted app launch, stop
speaking, and other deterministic local actions have not yet been implemented. Do not
interpret the existing Tier 0 name as meaning those commands exist.

### Recovery and production hardening

- Automatic Gmail/Calendar reconciliation for ambiguous writes is incomplete.
- Credentials are not yet migrated to an operating-system credential manager.
- Desktop IPC authentication and schema versioning do not exist yet.
- Installers, application signing, model asset management, and macOS packaging are
  not implemented.

## Milestones and roadmap

### Milestone 0 - Backend safety and latency foundation: complete

Delivered:

- calendar cancellation fix,
- correct prepare/draft/send email semantics,
- approval of exact payloads,
- crash-safe execution records,
- durable task artifacts,
- executable JSONL evals,
- tiered session coordination,
- latency instrumentation,
- contact-name correction improvements.

Exit evidence: the full suite recorded 105 passing tests during Milestone 2
benchmark-reporting work.

### Milestone 1 - Verified weather support: complete

Goal: make "How is the weather today?" return verified current data without relying
on the language model's memory.

Delivered:

1. Add typed weather request/result schemas and a `WeatherProvider` protocol.
2. Add a deterministic mock weather provider for tests.
3. Add one real provider with explicit timeout and error handling. Verify its current
   official API documentation before choosing or implementing it; Open-Meteo is a
   reasonable candidate but should not be assumed without that check.
4. Add `JARVIS_DEFAULT_LOCATION` to configuration and `.env.example`.
5. If no location is configured or supplied, ask one concise clarification question.
6. Route only supported weather intents to this provider. For unsupported fresh-data
   domains, state that live data is unavailable rather than letting the model invent
   it.
7. Add `tool.weather.read` latency timing.
8. Add unit tests and JSONL evals for supplied location, default location, missing
   location, provider timeout/failure, and follow-up context.
9. Keep weather read-only; this milestone must not change Gmail or Calendar write
   gates.

Definition of done:

- A configured default location makes "How is the weather today?" work directly.
- Without a default, Jarvis asks for location once and understands the next reply.
- Every forecast shown to the user came from the provider and includes enough source
  metadata or timestamping to establish freshness.
- Provider failures result in an honest, helpful response.
- The full suite and new eval cases pass.

Exit evidence: typed mock and Open-Meteo providers, timeout/failure coverage,
location clarification/defaulting, temporal follow-up context, source timestamps,
unsupported-fresh-data refusal, and `tool.weather.read` timing all pass in the
90-test suite.

### Milestone 2 - Measure and optimize the live voice loop: complete

Goal: improve perceived response time based on real measurements.

1. Run 10-20 representative turns with `python main.py --voice --latency`.
2. Record warm P50/P95 for capture, STT, model/tool work, TTS first audio, and total
   time.
3. Optimize the largest measured contributor first.
4. Re-run the same benchmark and record before/after results.

Likely later techniques include model preloading, endpoint detection, partial
transcripts, shorter first clauses, segmented synthesis, and overlapping generation
with playback. Do not perform a broad graph rewrite before the measurements identify
the bottleneck.

Current checkpoint:

- Readiness passed with Gemini, Google credentials, Faster-Whisper, Kokoro, microphone,
  and speakers available; Gmail send and Calendar writes remained locked.
- Cached startup measurement: `stt.load=878ms`, `tts.load=6521ms`.
- One live orchestration smoke turn measured `stt.transcribe=2607ms`,
  `llm.respond=5134ms`, `tts.first_audio=3414ms`, and
  `voice.time_to_first_response_audio=11180ms`. Its transcript was incorrect, so it is
  not a valid baseline sample or basis for optimization.
- `--latency-report PATH` now writes sanitized JSON metrics, and Ctrl+C exits the voice
  loop cleanly.
- `benchmarks/VOICE_BASELINE.md` defines two warm-up and twelve read-only measured
  prompts. The user must run this locally so the microphone interaction is reliable.
- The first measured baseline found warm `stt.transcribe` at P50/P95 404/436ms,
  `tts.first_audio` at 2631/6450ms, and speech-end-to-first-audio at 3482/9149ms.
  Several prompts were mistranscribed under `base.en`/beam 1.
- The accuracy profile is now `small.en`, beam 5, VAD, and a narrow domain prompt. The
  model is cached locally; CUDA decoding was rejected because `cublas64_12.dll` is not
  installed, so the reliable CPU/int8 path remains active.
- Kokoro now receives short sentence-sized chunks, language-model responses default to
  one or two short sentences, and visual weather-source metadata is not spoken.
- Fixed a discovered Calendar bug: "free tomorrow" now queries only tomorrow rather
  than starting from today. In Google mode, read requests use the authenticated primary
  Google Calendar; external writes remain locked.
- The first optimization reduced `tts.first_audio` P50/P95 from 2631/6450ms to
  1010/2619ms. Speech-end-to-first-audio P95 fell from 9149ms to 5955ms, while P50
  rose from 3482ms to 4000ms because the larger STT profile cost about one extra second.
- A short `distil-large-v3` validation did not improve the important recognition errors
  and raised `stt.transcribe` to 5718/7177ms P50/P95 on CPU. Its audio metadata exposed
  a very quiet microphone signal at roughly -40.6 to -43.2 dBFS RMS.
- The active model is back to cached `small.en`. STT now applies bounded gain toward
  -24 dBFS before both VAD and transcription, while reports retain raw peak/RMS and the
  applied gain without recording speech content.
- Phase A of `speechlayer.md` is implemented with a typed normalized utterance and
  conservative, context-bound deterministic repairs. The CLI prints both `Heard` and
  `Understood` when a repair occurs. Critical entities such as recipient names are not
  silently rewritten and continue through the existing clarification gates.
- The short `voice-normalized.json` run restored warm STT to 1406/2014ms P50/P95.
  The microphone remained quiet at -39.4 to -44.2 dBFS RMS, so bounded normalization
  applied 15.4 to 20dB gain. Four target phrases were recognized exactly; "Email Joo"
  was first heard as "female Joo" and correctly on retry.
- A narrow command-verb repair now maps the command-shaped "female <name> and say..."
  form to email without changing the recipient. Clear new requests received during a
  pending clarification/proposal now safely abandon the unsent workflow and begin a new
  turn, fixing the sleep prompt being consumed as a contact name.
- A live topic-switch smoke test confirmed that behavior: after an unresolved "Joo"
  contact clarification, a new sleep question abandoned the pending email and received a
  Tier-1 answer. No email was sent. The two warm STT samples were 1560/1669ms P50/P95;
  speech-end to first audio was 7344ms, with the model and TTS onset now dominating.
- Structured `STTResult` metadata now includes capture duration, raw level, applied gain,
  clipping, segment average log probability, no-speech probability, and a bounded quality
  heuristic. Obviously invalid captures are rejected before routing, and decoding uses
  explicit temperature zero.
- The complete `NormalizedUtterance` now reaches `SessionCoordinator`. Low-confidence
  ordinary conversation remains usable, while uncertain actions, approvals, and pending
  workflow answers ask for a repeat without invoking LangGraph or a provider. The default
  0.35 action threshold is configurable and intentionally described as uncalibrated.
- Voice-mode clarification prompts are now spoken rather than only printed. The full
  automated suite passes with 124 tests. The calibration result is recorded below;
  semantic LLM repair remains deferred.
- `voice-quality.json` produced correct text for all four captured utterances. Three
  requests scored 0.851-0.910 (`high`); "Cancel" scored 0.562 (`medium`) and safely used
  Tier 0. No clipping occurred and no-speech scores were near zero. Keep the 0.35 action
  threshold until a larger labeled sample exists. The intended silent capture was absent,
  so invalid-capture behavior is automated-test verified but not yet live verified.
- STT held at 1515/1678ms P50/P95 and normalization stayed below 1ms. Speech-end to first
  audio was 4444/6606ms; TTS total at 12227/20331ms remains the largest latency tail. This
  established the bounded contextual vocabulary store as the next accuracy slice.
- The bounded vocabulary store is now implemented. Explicit canonical terms and aliases
  persist in a gitignored JSON file with source, confidence, sensitivity, usage, and
  timestamps. Correction is longest-first and non-cascading; ambiguous aliases fail
  closed. Protected terms never enter correction or STT bias.
- Each capture selects at most 15 non-sensitive canonical terms using recent thread
  context and usage, then adds them only to Whisper's weak initial prompt. Reports expose
  only `vocabulary_count`. CLI add/list/remove controls are available, and the automated
  suite passes with 124 tests. Semantic repair remains deferred.
- The live vocabulary validation succeeded: with canonical `OmegaETH` and alias
  `omega eth`, Whisper directly emitted `OmegaETH`. The report recorded one injected
  term, confidence 0.755 (`high`), no clipping, and 1896ms STT. No post-transcription
  rewrite was needed. Response onset was 8683ms because Gemini took 5443ms; optimization
  should now return to model/TTS latency rather than adding semantic speech repair.
- Tier-1 Gemini conversation now requests an Interactions API event stream. Text deltas
  are assembled into the exact final response stored in the shared LangGraph thread.
  If streaming fails before text arrives, the existing non-streaming call is used; after
  text arrives, Jarvis preserves the partial response rather than risking a duplicate
  second answer.
- Voice output buffers complete sentences or bounded short clauses. A synthesis worker
  feeds a separate playback worker, allowing the next Kokoro chunk to synthesize while
  the current chunk is playing. Tier 2 routing, tool execution, and approval behavior are
  unchanged.
- New content-free stages are `llm.first_token`, `tts.synthesis`, and `tts.playback`;
  existing `tts.first_audio`, `tts.total`, and speech-end-to-first-audio measurements
  remain. Automated coverage verifies streaming event filtering, exact persistence,
  safe fallback/no-duplicate behavior, sentence buffering, and synthesis/playback
  overlap. The full suite passes with 124 tests.
- The live `voice-streaming.json` run streamed all six turns without fallback. For the
  four measured prompts, the first speakable response unit arrived about 343-370ms before
  generation completed and queued synthesis hid 0.7-4.3 seconds under playback. Onset
  P95 improved from 6,392ms to 5,203ms, but P50 rose from 3,963ms to 4,503ms because of
  model variance and one long first chunk. Playback remains the dominant response tail.
- Streamed Tier-1 speech now uses a 60-character cap and releases a natural clause at a
  comma or dash instead of waiting for a long sentence. Its voice-only prompt targets one
  sentence of at most 30 words without an unsolicited follow-up. Tier-2 and
  text-mode response policies remain unchanged. `tts.first_chunk_ready` separates model
  buffering from first-chunk synthesis. The compact validation report is
  `benchmarks/voice-compact.json`.
- The compact live run improved onset to 4,387/4,964ms P50/P95 and TTS total to
  9,912/18,167ms, but Gemini produced and persisted an incomplete second sentence ending
  at `Would`. Streamed calls now use 320 output tokens, require an explicit
  `interaction.completed` event, trim a dangling fragment after the last complete
  sentence, and prevent TTS from flushing the same tail. The targeted validation report
  is `benchmarks/voice-complete.json`.
- The repeated `voice-complete.json` validation produced six complete responses with no
  stream-interruption or incomplete-tail events. Gemini first token was 1,844/2,399ms
  P50/P95, full response was 2,162/2,724ms, first TTS audio was 1,330/1,410ms, and
  speech-end to first response audio was 4,614/5,231ms. An earlier run with 21-39 second
  model delays was not reproducible after the Gemini API issue was corrected.
- A Gemini completion envelope is now accepted only when its embedded interaction status
  is `completed`; an `incomplete` terminal status follows the existing partial-stream
  safety path. Deltas remain joined exactly without unsafe whitespace guessing. The full
  automated suite passes with 130 tests. A date-dependent Calendar eval discovered on a
  Friday was also changed to target an explicit future weekday while preserving the same
  clarification behavior.

### Milestone 3 - Windows desktop vertical slice: in progress

Goal: turn the reliable backend into an always-available Windows experience.

- Add a sibling Tauri `desktop/` app.
- Keep `jarvis/` as the backend and retain CLI support.
- Define versioned request/event schemas.
- Use a narrowly scoped local IPC channel, such as loopback HTTP/WebSocket protected
  by a per-launch random token, or stdio JSON-RPC.
- Implement backend process lifecycle, readiness, health, and crash reporting.
- Add tray controls, global hotkey, a minimal orb, transcript/status display, approval
  cards, and settings.
- Deliver Windows first. Treat macOS as a separate later packaging milestone.

Current checkpoint:

- `jarvis/ipc/` defines a versioned protocol and a newline-delimited stdio server. Stdio
  is private to the desktop-owned child process and does not open a loopback port.
- `python main.py --ipc-stdio` emits readiness, handles health checks and persistent text
  turns, forwards Tier-1 `assistant.delta` events, returns clarification/approval fields,
  reports malformed requests without exiting, and shuts down the owned backend cleanly.
- Capability metadata reflects the existing Gmail-send and Calendar-write locks without
  exposing configuration secrets. CLI text and voice modes remain intact.
- Desktop streaming explicitly uses the normal text response policy and 160-token limit;
  it does not inherit the compact voice-only prompt or its 320-token allowance.
- The sibling `desktop/` Tauri 2 scaffold now uses a vanilla TypeScript/Vite frontend and
  a Rust-owned Python child process. Rust validates outbound protocol envelopes, pipes
  backend JSONL events to the webview, keeps stderr out of the protocol channel, reports
  process exit, and requests graceful shutdown before forcing cleanup.
- The first UI has a compact orb/status surface, persistent transcript, streaming Tier-1
  text, text input, clarification display, and exact approval cards with approve/cancel
  controls.
- Desktop click-to-start/click-to-stop voice is now wired through `voice.start`,
  `voice.stop`, and `voice.cancel` IPC methods. `voice.state` drives loading, listening,
  transcribing, thinking, and speaking UI states; `voice.transcript` displays the
  normalized utterance and preserves the raw transcript when a correction was applied.
  Voice models are preloaded before backend readiness, then reused, so clicking an enabled
  microphone starts capture without paying model-load latency. The desktop path uses the
  same vocabulary, STT validation, conservative normalization, session thread, compact
  voice-response policy, streaming Kokoro queue, and safety gates as CLI voice.
- Windows child stdio is forced to UTF-8 in both Rust and Python. The Rust stdout reader
  consumes raw lines and survives malformed/non-UTF-8 output instead of abandoning the
  pipe. This fixes the observed `stream did not contain valid UTF-8` failure followed by
  Python `OSError: [Errno 22]` flush errors and an indefinitely busy UI.
- The next desktop smoke test exposed a separate model-stream completeness case: a fact
  ended at `roughly` even though the interaction reached a terminal state. Completed
  Gemini lifecycle payloads are now reconciled against streamed text, emitting only an
  exact authoritative suffix from final model-output steps. A voice stream that remains
  punctuation-incomplete is replaced with a retry response rather than persisted, and
  buffered partial TTS is aborted before that recovery is spoken.
- The following live pass identified three quality issues together. Voice-only Gemini
  streaming uses the validated `low` thinking level while text and structured
  tool work retain their configured policies. The observed non-entity substitution
  `Help me something interesting` is conservatively repaired to `Tell me something
  interesting` and the phrase is present in the default weak Whisper prompt. Streamed
  Kokoro responses now buffer up to 180 characters so the intended short sentence is
  normally synthesized as one utterance, and playback keeps one `sounddevice.OutputStream`
  open across all generated audio instead of reopening the device between chunks. This
  trades a small amount of first-chunk buffering for continuous, natural prosody.
- Desktop IPC enables content-free latency recording and prints one `Desktop voice
  latency:` line per successful turn to stderr. Use it to compare STT, Gemini first token,
  TTS onset, speech-end-to-first-audio, STT quality/confidence, and microphone RMS/peak
  without logging the utterance or response.
- `npm.cmd run build` passes with Vite 7.3.6, `cargo test` passes against Tauri 2.11.5,
  and the Python suite passes with 141 tests. Build outputs are ignored; npm and Cargo
  lockfiles are retained.
- The text desktop path has been smoke-tested live. The next step is a live desktop
  microphone/speaker smoke test, followed by tray/minimize behavior and a global show/hide
  hotkey. Automatic endpoint detection and barge-in remain in the later realtime-voice
  milestone.

### Milestone 4 - Structured long-term memory

Goal: give conversation durable, inspectable continuity.

- Implement explicit remember, recall, forget, and correct flows.
- Store provenance, confidence, sensitivity, timestamps, status, and supersession.
- Retrieve only relevant memories into prompts.
- Add settings to inspect, correct, and delete stored facts.
- Begin with structured storage; do not add embeddings until their value is measured.

### Milestone 5 - Provider-specific reconciliation

Goal: automatically resolve more uncertain Gmail and Calendar outcomes without
duplicating side effects.

- Introduce stable operation identifiers and provider metadata.
- Add provider-specific searches/verification for ambiguous sends and event writes.
- Verify the relevant official provider behavior before relying on identifiers or
  search semantics.
- Keep an action blocked when reconciliation cannot prove the outcome.

### Milestone 6 - Realtime voice conversation

Goal: replace the sequential push-to-talk feel with a conversational audio loop.

- Voice activity detection and automatic end-of-turn detection.
- Streaming or incremental transcription.
- Segmented/streaming synthesis and early first audio.
- Barge-in and immediate stop-speaking support.
- Concurrent, cancellable `AudioController` state.
- Explicit latency and interruption evals.

### Milestone 7 - Safe local OS actions

Goal: add deterministic, low-risk Windows commands.

- Volume and media control.
- Stop speaking.
- Open allowlisted applications or files.
- Strict schemas, allowlists, audit records, and confirmations based on risk.
- No unrestricted shell or general computer-control agent in this milestone.

### Milestone 8 - Production hardening and distribution

- Store credentials in the OS credential manager.
- Redact logs and define retention controls.
- Harden and authenticate local IPC.
- Manage/download voice model assets safely.
- Build signed Windows packages and an update strategy.
- Add macOS adapters, permissions, packaging, and signing afterward.

Longer-term work can include semantic/episodic memory, additional specialist tools,
a sandboxed computer agent, proactive event handling, and optional local language
models. These should follow the safe vertical slices above rather than precede them.

## File map

```text
jarvis/
  main.py                    CLI, startup checks, text/voice loops
  config.py                  environment-backed settings
  core/
    jarvis.py                top-level orchestration and route handling
    session.py               Tier 0/1/2 dispatch and turn coordination
    graph.py                 current LangGraph construction
    execution.py             idempotency, status, verification, recovery
    artifacts.py             durable exact proposals/task artifacts
    latency.py               tracing and P50/P95 metrics
    router.py                action/conversation classification
    brain.py                 model providers and structured generation
    permissions.py           confirmation policy
    state.py                 graph/session state
  agents/
    email/                   email intent, proposal, revision, execution
    calendar/                calendar intent, proposal, execution
  integrations/
    google_auth.py           OAuth and service construction
    gmail.py                 mock and Google Gmail providers
    contacts.py              mock and Google Contacts providers
    calendar.py              mock and Google Calendar providers
    weather.py               mock and Open-Meteo weather providers
  ipc/
    protocol.py              versioned request, response, event, and error envelopes
    server.py                desktop-owned JSONL stdio server and lifecycle
  memory/                    checkpoint and long-term memory foundations
  voice/
    stt.py                   Faster-Whisper capture/transcription
    tts.py                   queued Kokoro synthesis/playback and sentence buffering
    normalizer.py            typed utterance and conservative deterministic repair
    vocabulary.py            bounded explicit vocabulary persistence and selection
tests/
  evals/                     JSONL behavioral scenarios and format docs
  test_*.py                  unit/integration-style mock tests
```

Benchmark protocol:

```text
benchmarks/VOICE_BASELINE.md  fixed prompts and before/after comparison instructions
```

## Recommended first prompt for the next chat

Use this exact handoff prompt:

> Read `PROJECT_HANDOFF.md` and `claudev2.5.md`, then inspect the current working
> tree without discarding uncommitted changes. Milestone 1 weather support is complete
> and Milestone 2 is complete. Milestone 3 is in progress with 130 passing tests. The
> baseline, first TTS-onset
> optimization, and failed `distil-large-v3` validation are recorded in
> `benchmarks/VOICE_BASELINE.md`. The `voice-normalized.json` validation succeeded on
> the main speech prompts and exposed a pending-clarification topic-switch bug, which is
> now fixed along with the "female Joo" command-verb homophone. Structured STT quality,
> invalid-capture rejection, full `NormalizedUtterance` coordination, confidence-aware
> action gating, and spoken clarification prompts are implemented. `voice-quality.json`
> validated four correct utterances at 0.562-0.910 confidence without clipping or unsafe
> execution; keep the 0.35 threshold. The silent branch still lacks a live sample but has
> automated coverage. The bounded contextual vocabulary store and CLI add/list/remove
> controls are implemented without injecting protected terms or contact lists. The
> `voice-vocabulary.json` run live-validated canonical decoder bias with one injected term
> and no post-processing. Tier-1 Gemini output now streams into sentence-buffered Kokoro
> queues with synthesis overlapped against playback, while the exact assembled response
> is persisted in LangGraph. `voice-streaming.json` validated this without fallback and
> showed playback as the remaining dominant tail. The compact pass improved onset and
> total playback but exposed an incomplete response ending at `Would`. Streamed voice now
> has 320 output tokens, requires a genuinely completed terminal interaction status,
> trims dangling post-sentence fragments, and prevents TTS from flushing them.
> `voice-complete.json` validated six complete responses with Gemini first-token latency
> at 1,844/2,399ms P50/P95 and speech-end-to-first-audio at 4,614/5,231ms. Keep semantic
> LLM repair deferred. Versioned request/response/event schemas and a private JSONL stdio
> backend are implemented under `jarvis/ipc/`, including readiness, health, text turns,
> streamed deltas, structured errors, and graceful shutdown. Continue Milestone 3 with a
> sibling `desktop/` Tauri 2 app. Its vanilla TypeScript UI and Rust-owned Python process
> lifecycle are implemented and compile against Tauri 2.11.5. Desktop push-to-talk is
> wired through the same STT normalization, shared session thread, and streamed Kokoro
> path as CLI voice. Completed Gemini output now reconciles missing streamed suffixes,
> while an unrecoverable punctuation-incomplete voice response is rejected rather than
> persisted. Voice-only Gemini uses low thinking, the observed `Help me something
> interesting` substitution is repaired conservatively, and Kokoro uses natural
> sentence-sized synthesis over one continuous output stream. Desktop voice turns print
> sanitized stage timings. A live run exposed `llm.stream_fallback` on the attempted
> voice-only `minimal` thinking setting, so streaming is restored to the previously
> validated configured `low` level. Non-stream Tier-2 and fallback results now emit their
> full `assistant.delta` before blocking audio playback, making the response bubble appear
> before Jarvis starts speaking. The follow-up run contained no streaming fallbacks and
> improved speech onset to a 6,157ms median, with all conversational first tokens arriving
> in 1,397-2,780ms. An interrupted incomplete stream was safely rejected; replacement
> deltas now prevent its discarded partial text from lingering in the bubble. The full
> suite has 141 passing tests and the desktop production build passes. Run another live desktop
> microphone/speaker smoke test next, then add tray/minimize behavior and a global
> show/hide hotkey. Do not enable live Gmail/Calendar writes or reorganize the existing
> `jarvis/` backend.
