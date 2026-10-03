# Jarvis Project Handoff — 2026-09-14

Original checkpoint: 2026-09-14
Last updated: 2026-10-03

This is the primary continuation document for the next development chat. It records the
implemented state, live validation results, safety constraints, open work, milestone
status, and recommended roadmap as of 2026-10-03. The filename retains the original
handoff date so existing links remain valid.

## Read this first

- `claudev2.5.md` is the canonical product and architecture target.
- This dated handoff is the most current implementation record. `PROJECT_HANDOFF.md`,
  `CLAUDE.md`, and `claudev2.md` remain useful history but contain some stale status.
- The working tree contains substantial uncommitted work belonging to the user. Preserve
  it. Do not reset, check out, delete, or reorganize it.
- Keep the Python package in `jarvis/`. The desktop app is its sibling in `desktop/`.
- Never expose or commit `.env`, `credentials.json`, `google.token.json`, API keys,
  refresh tokens, local databases, or personal vocabulary data.
- Gmail sending and Calendar writes must remain locked unless the user explicitly chooses
  to enable and live-test them. Do not treat an approval prompt as authorization to change
  those feature-lock settings.
- Automated tests use mocks/fakes and must not send email or change a real calendar.

## Executive status

Jarvis is now a working Python 3.11+ text/voice assistant with a Windows Tauri desktop
client. It supports persistent conversation, safe email/calendar workflows, verified
weather reads, local speech recognition and synthesis, tiered routing, exact-payload
approval, durable execution records, and desktop push-to-talk.

Current milestone status:

| Milestone | Status | Summary |
| --- | --- | --- |
| 0. Backend safety and latency foundation | Complete | Routing, approvals, idempotency, persistence, evals, latency |
| 1. Verified weather support | Complete | Open-Meteo reads, location handling, failure safety |
| 2. Measured voice optimization | Complete | Accuracy profile, normalization, vocabulary, streaming, natural TTS |
| 3. Windows desktop vertical slice | Complete | Tauri app, voice, tray, hotkey, settings, diagnostics validated |
| 4. Structured long-term memory | Complete | Explicit lifecycle, safe retrieval, migration, desktop controls, and live validation |
| 5. Provider-specific reconciliation | Not started | Resolve ambiguous Gmail/Calendar writes safely |
| 6. Realtime voice conversation | Not started | Endpoint detection, incremental STT, barge-in, cancellation |
| 7. Safe local OS actions | Not started | Allowlisted deterministic Windows commands |
| 8. Production hardening/distribution | Not started | Credential store, sidecar packaging, signing, installers |

Latest verification:

- Python suite: **162 passing tests**.
- Focused memory/IPC suite: **23 passing tests**.
- Desktop TypeScript/Vite production build: passing with Vite 7.3.6.
- Rust/Tauri suite: **6 passing tests** against Tauri 2.11.5; Clippy passes with warnings denied.
- Latest live desktop run: Gemini streaming recovered with no fallback, and the response
  bubble now appears when speech begins instead of after playback ends.

## Progress added after the original 2026-09-14 checkpoint

The final standard desktop voice/bubble smoke test ran on 2026-09-19:

- A streamed Tier-1 conversational answer displayed its bubble when Jarvis began
  speaking rather than after playback completed.
- Tier-2 weather and a second Tier-2 response also displayed text before blocking speech
  playback completed.
- Tier-1 produced `llm.first_token` and did not use `llm.stream_fallback`.
- Speech-end-to-first-audio measured 8,817ms for the conversational sample, 5,660ms for
  weather, and 2,316ms for the short Tier-2 response.
- The conversation's 4,122ms first-token time was slower than the prior warm range, but a
  single provider sample is not treated as a regression.
- STT remained within 1,446–1,724ms.
- Microphone RMS fell back to -34.4 to -39.8 dBFS; the third capture scored 0.4312
  (`medium`). Input-device selection, Windows gain, and microphone distance should be
  checked before the next recognition benchmark.
- Ctrl+C shutdown did not reproduce the former invalid-UTF-8 or Python flush crash.
- No Gemini stream interruption occurred, so replacement of a rejected partial bubble
  remains covered by automated behavior rather than a second live reproduction.

This closes the normal desktop response-bubble validation.

The desktop-presence smoke test later on 2026-09-19 completed the tray/minimize slice:

- Closing the main window hid it without stopping the desktop or Python process.
- The Windows notification-area icon and Show/Hide/Quit menu rendered correctly.
- Restoring uses the existing window and therefore preserves the backend, shared thread,
  pending work, and preloaded voice models.
- Explicit tray Quit requested graceful backend shutdown and left no Jarvis, Python, or
  development-server process behind.
- The Rust suite increased to three tests, and Rust formatting, Clippy with warnings
  denied, the Vite production build, and all 141 Python tests passed.

The global-hotkey smoke test later on 2026-09-19 completed the summon/hide slice:

- `Ctrl+Alt+J` hid and restored the existing window without creating another backend.
- When Jarvis is obscured or minimized, the shortcut restores and focuses it rather than
  interpreting a merely visible window as a request to hide.
- A deliberate `Alt+Tab` registration conflict produced a nonfatal diagnostic while the
  desktop process and tray controls remained healthy.
- Explicit Quit unregisters shortcuts before stopping the Python child.
- The Rust suite increased to five tests; formatting, Clippy with warnings denied, and the
  Vite production build passed after the hotkey implementation.

The final Milestone 3 settings smoke test later on 2026-09-19 completed the Windows
desktop vertical slice:

- A compact gear panel shows backend state, operating mode, non-secret provider/model
  names, voice availability/STT model, and read-only email/calendar safety locks.
- Changing `Ctrl+Alt+J` to `Ctrl+Shift+J` registered immediately and updated the existing
  window lifecycle.
- A deliberate `Alt+Tab` conflict was rejected while `Ctrl+Shift+J` remained active and
  the input rolled back to that working value.
- Preferences persisted as only `version` and `toggle_shortcut` under the per-user Tauri
  configuration directory; no credentials, transcripts, or approval payloads are stored.
- A clean restart loaded `Ctrl+Shift+J` from saved preferences, and the tray Quit action
  again left no Jarvis, Python, Node, or Cargo process behind.
- The Rust suite increased to six tests. The targeted IPC suite, TypeScript build, Rust
  formatting, and Clippy passed during implementation; the final full suites also passed.

Milestone 3 is complete.

The first Milestone 4 backend slice completed on 2026-09-23:

- Replaced key/value memory with a versioned typed record containing a stable ID, kind,
  reason, provenance, confidence, sensitivity, lifecycle status, timestamps, and
  supersession links.
- Added an idempotent SQLite migration that preserves legacy `long_term_memory` rows and
  does not resurrect corrected or forgotten records on restart.
- Added explicit remember, recall, correct, and forget commands. They route
  deterministically without spending a language-model call.
- Corrections create a new record and supersede the old one. Forgetting is a recoverable
  status transition rather than a destructive delete.
- Ordinary remarks never create memory. Credential-like material is rejected at both the
  parser and storage boundary.
- Sensitive memories can be explicitly recalled but are excluded from automatic context.
  Relevant normal memories are passed to conversation as provenance-bearing, untrusted
  data and are not persisted into the conversation transcript.
- Low-confidence spoken memory mutations are rejected by the speech safety gate, and an
  explicit memory command safely abandons an unrelated pending workflow before running.
- Added lifecycle, conflict, sensitivity, migration, restart, context-isolation, routing,
  topic-switch, and declarative evaluation coverage. The focused suite passed 58 tests;
  the full Python suite passed all 155 tests.

The second Milestone 4 slice completed on 2026-09-24:

- Extended the private JSONL protocol with typed `memory.list`, `memory.correct`,
  `memory.forget`, and `memory.restore` requests. Mutations identify an exact record ID;
  no database path or credential is exposed.
- Added a compact Memory section to desktop settings with active records, lifecycle
  history, kind, sensitivity, provenance, and timestamps.
- Correction is an inline exact-value edit and returns both old and new records. Forget
  requires a second deliberate confirmation. Restore appears only for eligible forgotten
  records with no active replacement.
- Credential-like material is rejected on correction and restoration. Legacy credential
  rows are filtered out before desktop serialization.
- Memory controls remain context-only and do not create approval or execution authority
  for email, calendar, or device actions.
- Added IPC lifecycle, parameter-validation, credential-filtering, and migration tests.
  The full Python suite passed all 158 tests at implementation time; the current suite has
  162 passing tests. The TypeScript/Vite build, Rust suite, and Rust formatting check pass.

The first 2026-09-24 live attempt exposed a persisted-interrupt recovery defect rather
than a Gemini-key failure. The `desktop-main` thread still held an older email contact
clarification; resuming it replayed Contacts lookup, whose separate Google OAuth refresh
token had been revoked (`invalid_grant`). New clear requests now abandon the pending
LangGraph task directly without replaying external reads or writes. Revoked refresh tokens
also fall back to fresh interactive OAuth setup, and private IPC logs the exception class
to stderr without logging request content. The real `desktop-main` checkpoint was repaired,
the requested meeting preference was stored, and a streamed Gemini turn succeeded on that
same thread.

Milestone 4 live acceptance completed on 2026-10-03:

- Google Calendar read access and ordinary Gemini conversation worked in the repaired
  persistent desktop thread.
- Deterministic normal-memory recall returned the corrected active value.
- A dummy home address was stored with a visible Sensitive label, excluded from automatic
  value disclosure, and returned only by explicit recall.
- Explicit password storage was refused, and the desktop correction boundary rejected the
  same credential-like value without altering the existing record.
- Active/history inspection and the previously exercised correction, deliberate forget,
  restoration, and restart-persistence flows behaved as designed.
- Sensitive and credential-like memory turns are redacted when persisted, and model-facing
  history is scrubbed dynamically so older desktop transcripts cannot bypass the sensitive
  memory retrieval boundary. The focused 23-test suite and full 162-test suite pass.

Milestone 4 is complete. The next implementation milestone is provider-specific
reconciliation.

## Product and architecture implemented

The effective request path is:

```text
text or local microphone
        |
        v
SessionCoordinator
        |
        +-- Tier 0: deterministic approval/rejection of an existing proposal
        |
        +-- Tier 1: ordinary conversation through one direct model call
        |           persisted into the same LangGraph thread
        |
        `-- Tier 2: actions, ambiguity, and fresh data through Jarvis Core
                    |
                    +-- EmailAgent
                    +-- CalendarAgent
                    +-- verified Open-Meteo weather read
                    +-- explicit unsupported-fresh-data response
                    `-- safe conversation fallback
                              |
                              v
                    permission -> execute -> verify
```

Architectural invariants already preserved:

- Jarvis is the only user-facing persona.
- Specialists return typed results; they do not speak directly to the user.
- Tier 1 shares the same durable thread as Tier 2, so switching between conversation and
  email/calendar work does not lose context.
- Ambiguous actions and fresh-data questions fail toward Tier 2 rather than being guessed
  by the conversational model.
- Exact proposals survive interrupts and SQLite-backed restarts. Approval resumes the
  same artifact instead of silently regenerating it.
- External writes require deterministic permission checks and post-write verification.
- The Tauri desktop is a client of Jarvis Core, not a replacement for it. CLI text and
  voice modes remain supported.

## Work completed

### Core, routing, persistence, and safety

- Added `SessionCoordinator`, `TierDispatcher`, `TierDecision`, and Tier 0/1/2 execution.
- Direct Tier-1 conversation is written back into the shared LangGraph thread.
- Added SQLite LangGraph checkpoints for resumable short-term context.
- Added durable exact task artifacts for proposals and revisions.
- Added execution records with `executing`, `verified`, `failed`, and `uncertain` states.
- Stale or uncertain side effects are not automatically retried.
- Added reconciliation hooks; only an explicit safe-retry result permits another write.
- Added exact-payload approval hashing and duplicate execution prevention across restarts.
- Clear new requests can safely abandon a pending unsent clarification/proposal, fixing
  the case where a sleep question was consumed as an email recipient answer.
- Added trace IDs, tier/reason diagnostics, stage timings, P50/P95 summaries, and sanitized
  JSON reports without recording prompts, email bodies, or audio.
- Added executable JSONL behavioral evaluations for conversation, email, calendar,
  weather, clarification, approval, and failure behavior.

### Email and Contacts

Email intent is explicit:

- `prepare`: internal content proposal only.
- `save_draft`: create a real Gmail draft.
- `send`: send only the exact approved payload.

Implemented behavior includes contact resolution, ambiguity clarification, proposal
revision, draft creation, sending, provider verification, and fail-closed intent tests.
“Write” or “draft” does not imply “send.” Live sending remains feature-locked by default.

### Calendar

- Implemented Google and deterministic mock providers for reads, create, update, and
  cancellation.
- Fixed mock cancellation ownership and a duplicate Google-provider method that could
  override the real API implementation.
- Added approved cancellation and Google delete/404 verification coverage.
- Fixed “free tomorrow” so it queries tomorrow rather than starting today.
- In Google mode, calendar read prompts query the authenticated primary Google Calendar.
- Calendar writes remain feature-locked by default.

### Verified weather and fresh-data safety

- Added typed weather requests/results and mock/Open-Meteo providers.
- Added explicit request timeout and honest provider-failure responses.
- Supports supplied location, `JARVIS_DEFAULT_LOCATION`, or one clarification question.
- Supports narrow temporal weather follow-ups.
- Weather answers contain source and retrieval time; source metadata is displayed but not
  unnecessarily spoken.
- Unsupported current news, prices, scores, traffic, and air-quality requests fail closed
  instead of being answered from model memory.

### Voice recognition and normalization

- Current STT profile is cached Faster-Whisper `small.en`, CPU/int8, beam 5, VAD enabled.
- `distil-large-v3` was live-tested and rejected: it was much slower on CPU and did not
  fix the important recognition errors.
- Raw microphone level is measured; bounded gain toward -24 dBFS is applied before VAD
  and transcription without storing audio content.
- `STTResult` includes duration, peak/RMS, applied gain, clipping, segment quality,
  no-speech score, bounded confidence heuristic, and quality classification.
- Invalid captures are rejected before routing.
- Low-confidence conversation remains usable, while low-confidence actions, approvals,
  and pending-workflow answers require repetition. The action threshold remains 0.35.
- `NormalizedUtterance` travels through the coordinator instead of discarding confidence
  and correction metadata.
- Conservative deterministic repairs cover observed non-entity mistakes such as the
  command-shaped “female Joo and say...” -> “email Joo and say...” and “Help me something
  interesting” -> “Tell me something interesting.” Critical names are not guessed.
- Clarification prompts are spoken in voice mode.

### Personal vocabulary

- Added a gitignored JSON vocabulary store with canonical form, aliases, source,
  confidence, sensitivity, use count, and timestamps.
- Corrections are longest-first and non-cascading; ambiguous aliases fail closed.
- Sensitive/protected entries are stored for explicit management but are never
  automatically substituted or injected into decoding.
- At most 15 selected non-sensitive terms enter Whisper's weak initial prompt, using
  recent thread context and use history.
- CLI controls:

```powershell
python main.py --vocabulary-add "OmegaETH" --vocabulary-alias "omega eth"
python main.py --vocabulary-list
python main.py --vocabulary-remove "OmegaETH"
```

- Live validation confirmed Whisper directly emitted `OmegaETH` with one injected term;
  no post-transcription rewrite was required.

### Model streaming and speech synthesis

- Tier-1 Gemini conversation uses Interactions API event streaming.
- Only visible text deltas are forwarded; thought summaries are excluded.
- The exact assembled response is persisted in the shared LangGraph thread.
- A stream that fails before emitting text may safely use one non-stream fallback.
- After visible output begins, Jarvis never makes a duplicate model call.
- A completed lifecycle payload can supply an exact missing suffix when streamed text is
  its prefix; divergent text is never merged or guessed.
- Terminal status must genuinely be `completed`.
- Dangling post-sentence fragments are trimmed; fully punctuation-incomplete responses
  are rejected and replaced with a retry response rather than persisted.
- Voice-only responses use one complete short sentence, 320 output-token headroom, and
  the validated Gemini `low` thinking level.
- An attempted voice-only `minimal` thinking setting caused `llm.stream_fallback` on the
  configured endpoint and was reverted.
- Kokoro uses sentence-sized buffering up to 180 characters so short answers normally
  synthesize as one natural utterance rather than pausing mid-sentence.
- Synthesis and playback have separate workers, and playback keeps one PortAudio output
  stream open instead of reopening the device between arrays.
- The expected tradeoff is slightly later first synthesis for more natural prosody.

### Desktop IPC and Windows application

- Added a sibling Tauri 2 application in `desktop/` using vanilla TypeScript/Vite.
- Added versioned JSONL stdio IPC in `jarvis/ipc/`; it opens no network port.
- The Rust process owns the Python child, validates protocol envelopes, forwards events,
  separates stderr logs from stdout protocol traffic, reports exit, requests graceful
  shutdown, and performs cleanup.
- IPC supports readiness, health, persistent text turns, streamed deltas, voice start,
  voice stop, voice cancel, structured errors, and shutdown.
- Windows child stdio is forced to UTF-8. Rust reads raw lines and handles malformed or
  non-UTF-8 output without abandoning the pipe. This fixed the CP949/UTF-8 crash and
  Python `OSError: [Errno 22]` flush failure.
- Whisper and Kokoro preload before the backend advertises readiness.
- Desktop push-to-talk uses click once to record and click again to stop.
- Desktop voice uses the same STT, normalizer, vocabulary, thread, routing, approval, and
  Kokoro paths as CLI voice.
- UI includes readiness/status, orb, persistent transcript, text input, microphone,
  streaming assistant bubbles, and exact approval cards with approve/cancel controls.
- Tier-1 deltas render incrementally.
- Non-streamed Tier-2, clarification, approval, and fallback responses now publish their
  full bubble before blocking audio playback.
- Replacement deltas overwrite discarded partial text after an interrupted/trimmed
  stream instead of appending the recovery to it or leaving it visible until playback
  ends.
- Desktop voice writes sanitized per-turn timing and STT level/quality data to stderr.
- A native system-tray icon provides Show Jarvis, Hide Jarvis, and explicit Quit actions.
- Normal window close hides to the tray without stopping the backend or unloading voice
  models; tray Quit shuts down the Python child before exiting.
- A configurable global shortcut (`Ctrl+Alt+J` by default) toggles the existing window.
  Invalid or conflicting shortcuts are reported without disabling tray, text, or voice
  operation, and the current registration state is exposed to the desktop UI.
- The in-window settings/diagnostics panel exposes only non-secret runtime state and the
  desktop shortcut. Successful changes persist per user; failed changes preserve the last
  working registration. Gmail-send and Calendar-write locks remain read-only.

## Important bugs found and fixed during live validation

1. Poor `base.en` recognition under the original low-beam profile.
   - Moved to `small.en`, beam 5, VAD, weak domain prompt, and bounded gain.
2. “female Joo” interpreted as ordinary conversation.
   - Added a narrow command-verb repair without guessing the contact name.
3. A new topic was consumed as the answer to a pending contact clarification.
   - Clear new requests now abandon the unsent workflow safely.
4. Gemini streamed an answer ending at `Would` and it was persisted/spoken.
   - Added completion-status checks, dangling-tail trimming, TTS discard, and rejection of
     responses with no complete terminal sentence.
5. Gemini's visible deltas stopped at `roughly` despite a completed response envelope.
   - Reconcile only an exact missing suffix from authoritative final model-output steps.
6. Kokoro paused for roughly two seconds inside a sentence.
   - Increased the voice unit to 180 characters and kept one continuous output stream.
7. Desktop microphone initially appeared to buffer for a long time.
   - Preload voice models before readiness and fix the broken UTF-8 pipe.
8. Jarvis's bubble appeared only after speech finished.
   - Emit non-stream response text before blocking playback.
9. Voice-only Gemini `minimal` thinking caused fallback and slower turns.
   - Restore the validated configured `low` setting.
10. An interrupted rejected stream could leave partial text in the UI temporarily.
    - Add replacement-delta semantics in backend and desktop client.

## Voice benchmark history and latest result

The complete protocol and historical reports are documented in
`benchmarks/VOICE_BASELINE.md`.

Key history:

- Original `base.en` profile was fast but frequently mistranscribed speech.
- `small.en`/beam 5 materially improved recognition at roughly one additional second of
  STT time.
- First TTS work reduced first-audio P50/P95 from 2,631/6,450ms to 1,010/2,619ms.
- `distil-large-v3` took roughly 5.7–7.2 seconds for STT on CPU and was rejected.
- `voice-complete.json` validated six complete streamed responses with Gemini first token
  at 1,844/2,399ms P50/P95 and speech-end-to-first-audio at 4,614/5,231ms.

Latest repeatable multi-turn latency run on 2026-09-13:

- 10 total turns: 9 Tier-1 conversation, 1 Tier-2 weather.
- No `llm.stream_fallback` events.
- Conversational `llm.first_token`: 1,397–2,780ms.
- STT: 1,368–1,944ms.
- Speech-end-to-first-audio: 5,260–7,710ms; median approximately 6,157ms.
- Weather speech onset: 5,382ms, including a 2,293ms provider read.
- STT confidence: 0.7448–0.9477, all classified high.
- Microphone RMS improved from roughly -43/-44 dBFS to -20.1/-22.4 dBFS.
- Several microphone peaks reached about 0.99. Reduce Windows microphone gain slightly
  to preserve clipping headroom; do not raise it further.
- One turn recorded `llm.stream_interrupted` and
  `llm.incomplete_response_rejected`. The correctness guard worked; replacement-delta UI
  behavior was added afterward. Standard bubble timing was live-validated on 2026-09-19;
  the rare interrupted replacement path remains automated-test verified.
- TTS synthesis/first-audio remains the main onset cost after STT/model work, usually
  about 2.2–3.5 seconds for a complete natural sentence.
- TTS playback is the dominant total tail, but playback duration is Jarvis speaking, not
  silent waiting.

The 2026-09-19 follow-up confirmed the user-visible result: the response bubble appeared
when Jarvis started speaking. Its three speech-onset samples were 8,817ms, 5,660ms, and
2,316ms. See `benchmarks/VOICE_BASELINE.md` for the complete comparison and interpretation.

Do not optimize solely from one provider outlier. Preserve correctness and naturalness,
and compare repeatable warm P50/P95 measurements.

## Current warnings that are not failures

- Hugging Face authentication warning: Faster-Whisper and Kokoro obtain public model
  assets from the Hugging Face Hub. Inference runs locally. `HF_TOKEN` is optional and
  mainly provides authenticated download/rate-limit benefits; it should not materially
  change warm per-turn latency after models are cached.
- PyTorch LSTM dropout and `weight_norm` deprecation messages originate in the current
  Kokoro dependency path. They are warnings, not the cause of the observed delays.
- `STATUS_CONTROL_C_EXIT` after stopping `tauri dev` with Ctrl+C is expected.

## Known gaps and limitations

### Desktop follow-ups

- Standard streamed and non-stream response-bubble timing is live-validated. Replacement
  of an interrupted partial stream remains automated-test verified because no second
  provider interruption occurred during the validation run.
- There is no global push-to-talk hotkey; the global show/hide shortcut is complete.
- Python and local model dependencies are not yet packaged as a distributable sidecar.

### Voice gaps

- Interaction remains manual push-to-talk.
- No automatic endpoint detection.
- No incremental/streaming STT.
- No barge-in or immediate stop-speaking controller.
- No wake word.
- The current confidence score is a bounded heuristic, not a calibrated probability.
- Semantic LLM transcript repair remains intentionally deferred; critical entities must
  continue to fail closed.

### Memory gaps

- LangGraph/SQLite conversation persistence works.
- The structured backend supports explicit remember, recall, correct, and forget flows,
  history-preserving lifecycle state, migration, relevance filtering, and sensitivity.
- Retrieval is deliberately deterministic and lexical. Add embeddings only if measured
  retrieval quality demonstrates a need.
- Desktop settings expose active/history inspection, correction, deliberate forget, and
  eligible-record restoration without exposing storage paths or credentials.

### Integration/recovery gaps

- Generic uncertain-write handling exists, but automatic provider-specific Gmail and
  Calendar reconciliation is incomplete.
- Unsupported live-data domains remain unavailable by design.
- There is no automatic location/geolocation service; weather uses supplied/configured
  location.

### Local computer and production gaps

- No general Windows action adapter exists.
- No unrestricted shell or GUI-control agent should be added as a shortcut.
- Credentials remain in local files rather than an OS credential manager.
- Installer, sidecar bundling, code signing, update strategy, model-download UX, and
  macOS packaging are not implemented.

## Roadmap

### Milestone 0 — backend safety and latency foundation: complete

Delivered tiered coordination, durable context/artifacts, exact approval, idempotency,
uncertain-state safety, executable evals, latency instrumentation, and core bug fixes.

### Milestone 1 — verified weather support: complete

Delivered mock/Open-Meteo providers, source freshness, location clarification/defaulting,
timeouts, temporal follow-ups, and unsupported-fresh-data refusal.

### Milestone 2 — live voice measurement and optimization: complete

Delivered the accuracy-first STT profile, signal normalization, quality metadata,
confidence gating, deterministic normalization, personal vocabulary, Gemini streaming,
completion safeguards, queued Kokoro, continuous playback, and benchmark history.

Do not reopen Milestone 2 broadly unless repeatable live evidence identifies a regression.
Realtime STT and barge-in belong to Milestone 6.

### Milestone 3 — Windows desktop vertical slice: complete

Already delivered:

- Tauri/Vite shell and Rust-owned Python lifecycle.
- Private versioned stdio IPC.
- Readiness, health, errors, shutdown, and UTF-8 robustness.
- Persistent text conversation and streamed bubbles.
- Exact approval UI.
- Preloaded desktop push-to-talk using the shared backend voice/session path.
- Natural sentence playback and pre-playback bubble updates.
- Native tray Show/Hide/Quit actions and close-to-tray lifecycle.
- Configurable global show/hide shortcut with focus-aware toggling, conflict diagnostics,
  and shutdown cleanup.
- Minimal settings/diagnostics panel with per-user shortcut persistence, safe rollback,
  non-secret runtime details, and read-only safety-lock visibility.

The exit condition is met: Jarvis can remain running, be summoned without returning to a
terminal, accept text or push-to-talk, show/speak the response, display exact approvals,
and shut down cleanly while preserving its shared thread. Replacement of a rare
interrupted partial stream remains automated-test verified and may be observed if it
recurs naturally; it is not a desktop milestone blocker.

### Milestone 4 — structured long-term memory: complete

Already delivered:

- Explicit remember, recall, forget, and correct flows.
- Versioned records with provenance, confidence, sensitivity, status, timestamps, and
  supersession.
- Safe legacy SQLite migration and restart persistence.
- Relevant-only automatic retrieval with sensitive-memory exclusion.
- Credential refusal and low-confidence voice mutation gating.
- Deterministic lifecycle and behavioral evaluations.
- Typed private desktop IPC for list, correct, forget, and restore operations.
- Desktop active/history inspection, sensitive labels, inline correction, deliberate
  forget confirmation, and eligible-record restoration.
- Desktop filtering and mutation rejection for credential-like legacy data.

The exit condition is met: memory is explicit, versioned, inspectable, correctable,
recoverably forgettable, restart-persistent, sensitive by policy, and unable to authorize
external actions. Continue using structured lexical retrieval; add embeddings only if
measured retrieval quality needs them.

### Milestone 5 — provider-specific reconciliation

- Add stable operation identifiers and provider metadata.
- Reconcile uncertain Gmail sends/drafts and Calendar writes using verified provider
  behavior.
- Never retry if the remote outcome cannot be proven.

### Milestone 6 — realtime voice conversation

- Automatic end-of-turn/VAD controller.
- Incremental or streaming STT.
- Cancellable audio state machine.
- Barge-in and immediate stop speaking.
- Wake word only after the interaction controller is reliable.
- Add explicit interruption and time-to-first-audio evaluations per execution tier.

### Milestone 7 — safe local OS actions

- Add deterministic volume/media and stop-speaking commands.
- Add allowlisted app/file opening.
- Use strict schemas, allowlists, audit records, and risk-based confirmation.
- Do not expose an unrestricted shell.

### Milestone 8 — production hardening and distribution

- Move secrets to an OS credential manager.
- Define log redaction and retention.
- Package Python/native voice dependencies as a sidecar.
- Build model-download/cache UX.
- Create signed Windows installers and an update strategy.
- Treat macOS permissions, packaging, signing, and adapters as a separate follow-up.

Longer-term work may include more specialist agents, proactive event handling with quiet
hours, optional local LLMs, and a sandboxed ComputerAgent. These should follow the safe
vertical slices above.

## Exact next action

Begin Milestone 5 with provider-specific reconciliation:

1. Inventory the exact stable identifiers and verification reads available for Gmail
   drafts/sends and Google Calendar creates/updates/cancellations.
2. Extend execution records with provider operation IDs, remote resource IDs, attempt
   timestamps, and reconciliation status without storing credentials or message bodies.
3. Add provider-specific reconciliation methods that can prove `verified`, `not_applied`,
   or `uncertain`; never infer success from a timeout or generic transport error.
4. Permit retry only after a verified `not_applied` result. Leave unresolved outcomes
   blocked as `uncertain` and explain that state to the user.
5. Build deterministic fake-provider tests for success, timeout-before-write,
   timeout-after-write, duplicate prevention, restart recovery, and failed verification
   before any live write testing.
6. Keep Gmail sending and Calendar writes locked during implementation. Any later live
   write test requires a separate explicit user decision and exact-payload approval.

The 2026-09-19 smoke test also showed microphone RMS back at -34.4 to -39.8 dBFS after a
previous run near -20 dBFS. Check the selected input device, Windows gain, and microphone
distance before using recognition accuracy as a release criterion.

## Useful commands

From the repository root:

```powershell
python main.py --check
python main.py --text
python main.py --voice --latency
python main.py --voice --latency --latency-report benchmarks\voice-next.json --thread-id voice-next
python main.py --ipc-stdio
python -m pytest -q
python -m pytest tests/test_evals.py -vv
python main.py --setup-google
```

Desktop:

```powershell
cd desktop
npm.cmd install
npm.cmd run build
npm.cmd run tauri dev
cd src-tauri
cargo test
```

## Configuration reference

Use `.env.example` as the template. Important variable names include:

```dotenv
JARVIS_MODE=google
JARVIS_LLM_PROVIDER=gemini
GEMINI_API_KEY=
# HF_TOKEN=                         # optional for public model downloads
JARVIS_GEMINI_MODEL=gemini-3.8-flash
JARVIS_GEMINI_THINKING_LEVEL=low
JARVIS_STT_MODEL=small.en
JARVIS_STT_BEAM_SIZE=5
JARVIS_STT_HOTWORDS=Jarvis
JARVIS_STT_INITIAL_PROMPT=...
JARVIS_STT_VAD_FILTER=true
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

Do not copy actual secret values into this or any handoff document.

## Project map

```text
main.py                         root entry point
jarvis/main.py                  CLI, checks, vocabulary controls, stdio mode
jarvis/config.py                environment-backed configuration
jarvis/core/jarvis.py           top-level orchestration and route handling
jarvis/core/session.py          Tier 0/1/2 dispatch and shared-thread coordination
jarvis/core/graph.py            current LangGraph construction
jarvis/core/brain.py            Gemini/OpenAI model adapters and streaming
jarvis/core/execution.py        execution status, idempotency, verification, recovery
jarvis/core/artifacts.py        durable exact proposals/task artifacts
jarvis/core/latency.py          trace-aware timing and percentile reports
jarvis/core/router.py           deterministic/conservative intent routing
jarvis/core/permissions.py      action confirmation policy
jarvis/core/state.py            graph/session state
jarvis/agents/email/            email intent, drafting, revision, execution
jarvis/agents/calendar/         calendar intent, proposal, execution
jarvis/integrations/gmail.py    mock and Google Gmail providers
jarvis/integrations/contacts.py mock and Google Contacts providers
jarvis/integrations/calendar.py mock and Google Calendar providers
jarvis/integrations/weather.py  mock and Open-Meteo providers
jarvis/ipc/protocol.py          versioned JSONL envelopes
jarvis/ipc/server.py            desktop-owned stdio server
jarvis/ipc/voice.py             desktop voice adapter and event sequencing
jarvis/voice/stt.py             Faster-Whisper capture/transcription
jarvis/voice/normalizer.py      typed utterance and safe deterministic repair
jarvis/voice/vocabulary.py      bounded personal vocabulary
jarvis/voice/tts.py             queued Kokoro synthesis and continuous playback
jarvis/memory/                  short-term and long-term memory foundations
desktop/src/main.ts             desktop UI and IPC event handling
desktop/src/styles.css          desktop visual styling
desktop/src-tauri/src/lib.rs    Rust child-process and Tauri bridge
tests/evals/                    declarative behavioral cases
tests/test_*.py                 unit/integration-style mock coverage
benchmarks/VOICE_BASELINE.md    complete voice benchmark history and protocol
```

## Non-negotiable engineering rules

1. Jarvis remains the sole user-facing persona.
2. Conversation and banter remain first-class paths.
3. Prefer tools over unnecessary agents and stable APIs over GUI automation.
4. The fast command gate must fail closed.
5. Resolve identity, time, and ambiguity before approval or execution.
6. Approval covers the exact resolved payload.
7. “Write/draft” is not “send.”
8. Never fabricate current external facts, recipients, dates, memories, or tool success.
9. Verify external side effects before reporting success.
10. Preserve unlimited clarification/edit/approval cycles and safe topic switching.
11. Keep STT/TTS outside domain-agent logic and load local models once.
12. External systems remain the source of truth for their current state.
13. Keep model providers replaceable.
14. Keep the desktop shell separate from the backend and OS behavior behind adapters.
15. Optimize perceived time-to-first-spoken-audio without sacrificing correctness,
    safety, or natural speech.
16. Do not add orchestration complexity merely for appearance.

## Suggested prompt for the next chat

> Read `PROJECT_HANDOFF_2026_09_14.md` and `claudev2.5.md` completely, then inspect the
> current working tree without discarding uncommitted changes. Treat the dated handoff as
> the current implementation record. Milestones 0 through 4 are complete; Milestone 5 is
> next. The Python suite has 162 passing tests, the desktop Vite build passes, and
> the latest desktop run restored Gemini streaming with 1,397–2,780ms first-token times
> and roughly 6,157ms median speech onset. A 2026-09-19 smoke test confirmed that normal
> Tier-1 and Tier-2 response bubbles now appear when speech begins instead of after
> playback. Replacement handling for a rare interrupted/incomplete stream remains
> automated-test verified because the interruption did not recur. Tray Show/Hide/Quit,
> close-to-tray, the configurable global shortcut, and the minimal settings/diagnostics
> panel are live-validated, including conflict rollback and persistence across restart.
> The structured memory backend, legacy migration, explicit lifecycle, relevance filter,
> sensitive-memory exclusion, deterministic routing, typed desktop IPC, and settings
> controls for inspect/correct/forget/restore are implemented and live-validated.
> A persisted-interrupt replay defect and revoked Google Workspace OAuth token were found;
> safe pending-task abandonment and revoked-token reauthorization are now implemented.
> Begin provider-specific reconciliation with fake-provider tests and keep Gmail sending
> and Calendar writes locked. Preserve the shared LangGraph thread, exact approval
> safeguards, CLI paths, and
> locked Gmail-send/Calendar-write settings. Do not reorganize `jarvis/` or expose any
> credentials.
