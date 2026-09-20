# Voice latency baseline protocol

Use the same microphone, speaker, room, network, and configuration for the before and
after runs. Gmail sending and Calendar writes must remain locked.

Run:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-before.json --thread-id voice-benchmark-before
```

Speak the two warm-up prompts first, then the twelve measured prompts. Check the printed
transcript after every turn. Retry a prompt immediately if its meaning was transcribed
incorrectly, and note that retry when reviewing the report. Say `quit` as the next voice
turn after the last response, or press Ctrl+C; either path writes the JSON report.

Warm-up prompts (exclude these first two trace IDs from comparisons):

1. "Hello, Jarvis."
2. "Tell me something interesting."

Measured Tier-1 conversation prompts:

1. "Good morning, Jarvis."
2. "Tell me one useful fact about sleep."
3. "I had a difficult interview today."
4. "Thank you."

Measured weather/read prompts:

1. "What's the weather in Seoul today?"
2. "What's the forecast for Seoul tomorrow?"
3. "What's the temperature in Busan today?"
4. "Will it rain in Seoul tomorrow?"

Measured Calendar read prompts:

1. "What's on my calendar today?"
2. "What's tomorrow looking like?"
3. "When am I free tomorrow?"
4. "When am I free next week?"

Compare warm P50/P95 for `stt.transcribe`, `tier.1.total`, `tier.2.total`, `llm.respond`,
`tool.weather.read`, `tool.calendar.read`, `tts.first_audio`,
`voice.time_to_first_response_audio`, and `tts.total`. Treat `audio.capture` as user
speaking time, not system latency. `stt.load` and `tts.load` are startup-only metrics.

Initial orchestration smoke run on 2026-09-07 (not a valid distribution because the
transcript was incorrect):

| Metric | Result |
| --- | ---: |
| `stt.load` | 1,213 ms |
| `tts.load` | 3,873 ms |
| `stt.transcribe` | 2,607 ms |
| `llm.respond` | 5,134 ms |
| `tts.first_audio` | 3,414 ms |
| `voice.time_to_first_response_audio` | 11,180 ms |
| `tts.total` | 19,749 ms |

The smoke transcript was "I want to make a good win" rather than the requested phrase,
so these values only prove the instrumentation path and must not be used as the baseline.

## First measured baseline

The user completed the prompt set on 2026-09-07 with `base.en`, beam 1. The session was
captured from terminal output because it had not yet exited to write the JSON report.
Warm-ups and mistranscribed retries were excluded. Eleven usable turns remained:

| Metric | Samples | P50 | P95 |
| --- | ---: | ---: | ---: |
| `stt.transcribe` | 11 | 404 ms | 436 ms |
| `llm.respond` | 4 | 2,043 ms | 3,045 ms |
| `tool.weather.read` | 3 | 2,261 ms | 2,261 ms |
| `tool.calendar.read` | 4 | 297 ms | 1,137 ms |
| `tier.1.total` | 4 | 2,053 ms | 3,053 ms |
| `tier.2.total` | 7 | 1,149 ms | 2,286 ms |
| `tts.first_audio` | 11 | 2,631 ms | 6,450 ms |
| `voice.time_to_first_response_audio` | 11 | 3,482 ms | 9,149 ms |
| `tts.total` | 11 | 12,973 ms | 31,234 ms |

Observed recognition failures included "difficult" becoming "practical" and "rain in
Seoul" becoming "rate and so." After this baseline, the accuracy profile changed to
`small.en`, beam 5, VAD, and a narrow assistant-domain prompt. Speech output now uses
shorter responses and sentence-sized Kokoro chunks; displayed source metadata is not
spoken. Run the post-change comparison with:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-after.json --thread-id voice-benchmark-after
```

## First optimization result

Eleven semantically usable turns were compared from each run, excluding warm-ups and
failed retries:

| Metric | Before P50/P95 | After P50/P95 | Result |
| --- | ---: | ---: | --- |
| `stt.transcribe` | 404 / 436 ms | 1,366 / 1,911 ms | slower accuracy profile |
| `llm.respond` | 2,043 / 3,045 ms | 1,644 / 1,974 ms | improved |
| `tier.1.total` | 2,053 / 3,053 ms | 1,651 / 1,990 ms | improved |
| `tier.2.total` | 1,149 / 2,286 ms | 829 / 2,316 ms | similar |
| `tts.first_audio` | 2,631 / 6,450 ms | 1,010 / 2,619 ms | substantially improved |
| `voice.time_to_first_response_audio` | 3,482 / 9,149 ms | 4,000 / 5,955 ms | tighter tail, slower median |
| `tts.total` | 12,973 / 31,234 ms | 12,067 / 22,320 ms | improved |

Recognition still failed initial attempts for "thank you" and "what's tomorrow looking
like." The former was caused by the `Joo` hotword being over-weighted. The next accuracy
iteration removed that hotword while retaining `Joo` as a weaker prompt hint and routed
rain/snow deterministically.

## Large-model accuracy validation

A short `distil-large-v3` run was captured in `benchmarks/voice-accuracy.json`. It did
not improve the important recognition failures and made CPU transcription much slower:
`stt.transcribe` was 5,718/7,177 ms P50/P95. The captured signal was also unusually
quiet, measuring about -40.6 to -43.2 dBFS RMS with peaks from 0.089 to 0.145. This made
the audio input, rather than model capacity alone, the next problem to address.

The active profile has therefore returned to cached `small.en`. Before transcription,
quiet audio is now raised toward -24 dBFS with a 10x gain ceiling and a 0.95 peak ceiling;
the normalized signal is also what VAD receives. A conservative Phase-A speech layer
repairs observed, context-bound non-entity phrases such as `and soul` in a rain question
and prints the repaired interpretation as `Understood`. Names and other critical action
entities remain untouched so downstream clarification stays fail-closed.

The short normalized-input validation was captured in `benchmarks/voice-normalized.json`:

| Metric | P50 | P95 |
| --- | ---: | ---: |
| `stt.transcribe` | 1,406 ms | 2,014 ms |
| `speech.normalize` | <1 ms | 1 ms |
| `tts.first_audio` | 1,142 ms | 1,591 ms |
| `voice.time_to_first_response_audio` | 4,847 ms | 8,697 ms |
| `tts.total` | 7,907 ms | 20,436 ms |

The microphone remained quiet at -39.4 to -44.2 dBFS RMS, and bounded normalization
applied 15.4 to 20 dB of gain. `small.en` correctly recognized "Thank you," the Seoul
weather question, the Calendar question, and the sleep question. "Email Joo" was first
heard as "female Joo" and then correctly on retry. A narrow command-verb repair now
handles that first form without changing the recipient.

The final sleep prompt exposed a separate session issue: it arrived while Jarvis was
waiting for a corrected contact and was consumed as the contact answer. The coordinator
now recognizes clear new-request forms, safely cancels the abandoned pending workflow,
and starts the new request without performing an external write. This behavior is covered
for both clarification and proposal states.

A live topic-switch smoke test on 2026-09-09 confirmed the fix. After Jarvis could not
resolve "Joo" and requested a full name or address, "Tell me one useful fact about sleep"
abandoned the pending email and received a normal Tier-1 answer. `pending.abandon` took
365 ms, warm STT was 1,560/1,669 ms P50/P95 across the two captures, and no email was
sent. Speech-end to first response audio was 7,344 ms; model response (2,737 ms) and TTS
first audio (2,781 ms) remain the main onset costs in that small run.

The original validation command was:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-normalized.json --thread-id voice-normalized
```

The structured-quality slice is now implemented. Faster-Whisper returns an `STTResult`
with capture duration, level/gain, clipping, segment log probability, no-speech
probability, and a bounded quality heuristic. Empty, shorter-than-250ms, silent, heavily
clipped, and blank-transcript captures are rejected. The complete `NormalizedUtterance`
reaches the Session Coordinator; uncertain ordinary conversation may proceed, while an
uncertain action, approval, or pending-workflow answer must be repeated before routing.
The default action threshold is 0.35 and is configurable because the score is a heuristic,
not a calibrated probability. Voice clarification prompts are now spoken as well as
displayed.

Capture a short calibration run before selecting dynamic-vocabulary or semantic-repair
thresholds:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-quality.json --thread-id voice-quality
```

Include one deliberately empty/very short capture, one ordinary conversation prompt, one
weather read, and one email request that is cancelled. Inspect only the content-free STT
metadata and whether unsafe/invalid input is repeated; do not tune the threshold from a
single sample. The current full automated suite passes with 124 tests.

The 2026-09-09 `voice-quality.json` calibration contained four valid captures (the
deliberately silent/short capture was not present). All transcripts were correct. The
sleep, weather, and email requests scored 0.881, 0.910, and 0.851 respectively (`high`);
"Cancel" scored 0.562 (`medium`) and safely completed Tier 0 without sending. Average
no-speech probabilities were 0.0003-0.0008 and no clipping was observed. Raw input
remained very quiet at -41.2 to -47.3 dBFS, requiring 17.2-20 dB gain.

Keep the action threshold at 0.35 until a larger labeled sample includes both incorrect
and valid low-score commands. STT was stable at 1,515/1,678 ms P50/P95 and normalization
remained below 1 ms. Speech-end to first audio was 4,444/6,606 ms; TTS total remained the
largest user-visible tail at 12,227/20,331 ms. This run validates metadata propagation and
safe approval handling, but not the live invalid-capture branch.

## Contextual vocabulary slice

The bounded explicit vocabulary store is implemented in `jarvis/voice/vocabulary.py`.
It persists canonical terms, aliases, source, explicit status, usage, confidence,
sensitivity, and timestamps in a gitignored local JSON file. Automatic correction uses a
single non-cascading, longest-alias-first pass. Conflicting aliases are rejected, entries
must exceed 0.8 confidence, and protected entries never participate in substitution or STT
bias.

Before each capture, up to 15 non-sensitive canonical terms are ranked by recent thread
context, explicit status, usage, confidence, and specificity. They are added only to the
weak Faster-Whisper initial prompt; dynamic terms are never promoted to strong hotwords.
Latency reports record only the selected count, never vocabulary contents.

Validate with a real recurring non-sensitive term (the example below may be replaced):

```powershell
python main.py --vocabulary-add "OmegaETH" --vocabulary-alias "omega eth"
python main.py --vocabulary-list
python main.py --voice --latency --latency-report benchmarks/voice-vocabulary.json --thread-id voice-vocabulary
```

Say the alias in a natural sentence and check `Heard`/`Understood`; the report should show
`vocabulary_count` without revealing the term. Remove the example afterward if it is not
useful with `python main.py --vocabulary-remove "OmegaETH"`. The full automated suite
passes with 124 tests.

The 2026-09-09 live validation succeeded. With `OmegaETH` stored as the canonical form
and `omega eth` as its alias, Faster-Whisper emitted `OmegaETH` directly. The report
recorded `vocabulary_count=1`, confidence 0.755 (`high`), no-speech probability 0.0011,
and no clipping. The dictionary usage count correctly remained zero because deterministic
post-processing was unnecessary. STT took 1,896 ms. Overall response onset was still
8,683 ms because the language-model response took 5,443 ms and TTS onset took 1,328 ms;
the next optimization target is response/TTS latency rather than more speech repair.

## Tier-1 streaming and queued-TTS validation

Tier-1 Gemini responses now stream as text deltas. Complete sentences or bounded short
clauses enter a Kokoro synthesis queue immediately; a separate playback queue allows the
next chunk to synthesize while the current chunk is playing. The exact assembled reply is
still written to the shared LangGraph thread. Tier 2 tools and approval flows remain on
the complete-response path.

Run the four measured Tier-1 prompts near the top of this file with:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-streaming.json --thread-id voice-streaming
```

Compare `voice.time_to_first_response_audio` against `voice-vocabulary.json` and the
earlier baseline reports. Inspect `llm.first_token`, `llm.respond`, `tts.first_audio`,
`tts.synthesis`, `tts.playback`, and `tts.total` to distinguish model delay, first-chunk
synthesis, playback duration, and overlap. `tts.synthesis` and `tts.playback` may have
multiple samples per turn because they are recorded per chunk. The first run also pays
the existing model-loading costs, so use a warm prompt before judging onset.

The 2026-09-09 `voice-streaming.json` run successfully streamed all six turns without a
fallback or interruption. Across the four measured prompts, streaming made the first
complete spoken chunk available about 343-370ms before the full model response finished.
Synthesis/playback overlap hid 0.7-4.3 seconds per response. Compared with the matching
four prompts in `voice-after.json`, speech-end-to-first-audio P95 improved from 6,392ms to
5,203ms, although P50 rose from 3,963ms to 4,503ms due to model variance and one
1,994ms first-chunk synthesis. TTS total improved from 14,856/20,867ms P50/P95 to
13,007/20,086ms. Playback duration remains the dominant tail.

The next bounded pass uses a 60-character cap only for streamed Tier-1 audio, releases a
natural comma/dash/clause before a long sentence completes, and asks streamed voice
responses to stay near 30 words. Non-streaming Tier-2 speech retains its 90-character
chunking. `tts.first_chunk_ready` now measures the time from post-STT stream setup until
the first speakable unit is queued, separating response buffering from first-chunk
synthesis.

Validate the same two warm-ups and four measured Tier-1 prompts with:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-compact.json --thread-id voice-compact
```

The target is warm speech onset below four seconds with a smaller playback tail.

The 2026-09-09 `voice-compact.json` run contained the four measured prompts without the
two warm-ups. Compared with `voice-streaming.json`, speech-end-to-first-audio improved
from 4,503/5,203ms P50/P95 to 4,387/4,964ms, first-audio P95 improved from 1,994ms to
1,195ms, and TTS total improved from 13,007/20,086ms to 9,912/18,167ms. The new
`tts.first_chunk_ready` metric showed that three of four first chunks were ready within
about 1ms of the first model text; the sleep response waited 171ms for a natural boundary.

One response exposed a correctness failure: Gemini ended normally after `Would`, and the
incomplete fragment was persisted and spoken. Streamed voice calls now have 320 output
tokens of headroom, must observe an explicit `interaction.completed` event, and remove a
dangling fragment when a complete sentence precedes it. The TTS stream independently
discards that tail instead of flushing it. The compact voice prompt now requests one
complete sentence and prohibits unsolicited follow-up questions.

Run a short correctness validation with:

```powershell
python main.py --voice --latency --latency-report benchmarks/voice-complete.json --thread-id voice-complete
```

After one warm-up, say "I had a difficult interview today," "Tell me one useful fact
about sleep," and "Thank you." Each response must end as a complete thought. The report
must contain no `llm.stream_interrupted`, `llm.incomplete_tail_trimmed`, or
`tts.incomplete_tail_discarded` event during a normal run. The full automated suite passes
with 130 tests.

## Completion correctness result

The repeated `voice-complete.json` run on 2026-09-11 contained six complete Tier-1
responses and no `llm.stream_interrupted`, `llm.incomplete_tail_trimmed`, or
`tts.incomplete_tail_discarded` events. An earlier run showed anomalous Gemini delays of
21-39 seconds before first text; after the Gemini API issue was corrected, the same
configuration returned to the expected range:

| Metric | Samples | P50 | P95 |
| --- | ---: | ---: | ---: |
| `stt.transcribe` | 6 | 1,343 ms | 1,472 ms |
| `llm.first_token` | 6 | 1,844 ms | 2,399 ms |
| `llm.respond` | 6 | 2,162 ms | 2,724 ms |
| `tts.first_chunk_ready` | 6 | 1,986 ms | 2,400 ms |
| `tts.first_audio` | 6 | 1,330 ms | 1,410 ms |
| `voice.time_to_first_response_audio` | 6 | 4,614 ms | 5,231 ms |
| `tts.total` | 6 | 10,293 ms | 14,240 ms |

Gemini terminal events are now accepted only when the embedded interaction status is
`completed`; `incomplete` follows the existing interrupted-stream safety path. Stream
deltas are concatenated exactly, including provider-supplied whitespace, so Jarvis does
not guess word boundaries in a way that could damage punctuation, URLs, code, or
non-English text. This closes the measured push-to-talk optimization milestone; automatic
endpoint detection, incremental STT, barge-in, and cancellation remain part of the later
realtime-voice milestone.

## Desktop completion reconciliation

A 2026-09-12 desktop voice smoke test returned a completed interaction whose visible
deltas stopped at `Remarkably, sharks have inhabited our oceans for roughly`. The Gemini
adapter now checks model-output text included in the completed lifecycle payload. When
the streamed text is an exact prefix, only the authoritative missing suffix is emitted;
divergent text is never merged. If a voice stream still ends without terminal punctuation
and no complete sentence can be retained, Jarvis discards the fragment, persists a clear
retry response, and aborts buffered partial audio before speaking that recovery. This
preserves streaming latency without inventing a factual ending.

## Desktop responsiveness and naturalness pass

The next 2026-09-12 desktop smoke test showed three coupled issues: the ordinary voice
turn could begin faster, Whisper produced `Help me something interesting`, and Kokoro
paused for roughly two seconds inside a sentence. The bounded correction now maps only
that grammatically incomplete observed phrase to `Tell me something interesting`; it does
not rewrite `Help me with something interesting` or any critical entity. The default weak
Whisper prompt also includes the intended phrase while retaining `small.en` beam-5 for the
validated accuracy profile.

Voice-only Gemini streaming uses the validated `low` thinking level; normal text and structured
tool calls retain their existing settings. Kokoro's former 60-character early-release
policy is superseded by a 180-character cap so the requested short voice sentence is
normally synthesized as one utterance. Playback now uses one continuous PortAudio output
stream rather than reopening the device for each generated array. The expected tradeoff
is slightly later chunk readiness in exchange for no artificial mid-sentence device gap
or prosody reset. Desktop runs now print a sanitized `Desktop voice latency:` summary for
each successful turn, including STT quality/confidence and microphone RMS/peak, so this
tradeoff and the previously quiet input can be measured live.

## Desktop diagnostic result and UI timing correction

The 2026-09-13 live desktop run recorded conversational speech onset at 9,050ms and
7,746ms, and weather speech onset at 5,385ms. Both conversational turns contained an
`llm.stream_fallback` marker and no `llm.first_token`: the attempted voice-only `minimal`
thinking setting did not produce a usable stream on the configured model, so Jarvis made
a second non-streaming model call. Voice streaming has therefore returned to the
previously validated `low` level. The zero-millisecond fallback value is an instantaneous
event marker, not the time spent in the replacement call.

The three STT calls took 1,414-1,670ms and were classified high quality, but the second
confidence was borderline at 0.659. Input RMS remained very quiet at -42.7 to -44.0 dBFS
while peaks of 0.0771-0.0967 showed no clipping. Raising the Windows microphone input
level or reducing microphone distance should improve recognition more than further
decoder weakening.

Non-streamed responses previously emitted no `assistant.delta`; the desktop received the
terminal response only after blocking TTS playback completed. Tier-2 responses and model
fallbacks now emit their completed text before playback starts, so the response bubble
leaves its loading state before Jarvis speaks. Genuine Tier-1 streams continue to render
incremental deltas. The full automated suite passes with 141 tests.

The follow-up 2026-09-13 desktop run confirmed that restoring `low` removed the
regression: all nine conversational turns recorded `llm.first_token` (1,397-2,780ms),
and none recorded `llm.stream_fallback`. Across all ten turns, STT took 1,368-1,944ms
and speech-end-to-first-audio fell to 5,260-7,710ms with a median of about 6,157ms.
The microphone was much stronger at -20.1 to -22.4 dBFS and confidence ranged from
0.7448 to 0.9477. Several peaks reached 0.99, so the input should now be reduced slightly
to preserve headroom rather than raised further.

One turn recorded `llm.stream_interrupted` followed by
`llm.incomplete_response_rejected`. This is the intended correctness guard: Gemini
started a response but did not supply a safe complete result. Desktop delta events now
support a replacement flag, allowing the recovery or trimmed authoritative response to
replace already-rendered partial text immediately. Targeted IPC/model tests, the complete
141-test Python suite, and the TypeScript/Vite production build pass.

## Desktop response-bubble validation

The 2026-09-19 live desktop smoke test confirmed that assistant text now appears when
Jarvis begins speaking instead of remaining as an empty loading bubble until playback
ends. The run covered one streamed Tier-1 conversation and two Tier-2 turns, including
weather. Tier-1 produced `llm.first_token` without fallback; Tier-2 text was available to
the desktop before its blocking playback. Shutdown also completed without the former
invalid-UTF-8 or Python flush failure.

The three observed speech-end-to-first-audio values were 8,817ms, 5,660ms, and 2,316ms.
The first conversation was slower because Gemini first-token latency rose to 4,122ms in
that sample; one provider sample is not evidence of a new regression. STT remained at
1,446-1,724ms. Microphone RMS was again quiet at -34.4 to -39.8 dBFS, and the third
capture produced only 0.4312 confidence (`medium`). Check Windows input level,
microphone selection, and distance before the next accuracy benchmark. No interrupted
stream occurred, so replacement of a rejected partial response remains automated-test
verified rather than live-reproduced.
