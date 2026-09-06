# Jarvis V0.1

Jarvis is a persistent, conversational assistant with specialist email and calendar
capabilities. The implementation follows [CLAUDE.md](CLAUDE.md): Jarvis is the only
user-facing persona, normal conversation is a first-class path, specialists exchange
typed data, and externally visible actions require approval of the exact payload.

The project supports two modes. `mock` is the safe default for tests and offline
development. `google` uses Gemini 3.8 Flash by default, Google People, Gmail, Google
Calendar, and durable SQLite checkpoints. OpenAI remains an optional model provider.
Live email sending and calendar changes have
independent feature locks in addition to Jarvis's exact-payload approvals.

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
from jarvis import Jarvis

jarvis = Jarvis()
result = jarvis.turn("Email David a quick update", thread_id="demo")
while result.needs_input:
    print(result.prompt)
    result = jarvis.resume(input("> "), thread_id="demo")
print(result.response)
```

Run tests with:

```powershell
pytest
```

## Safety model

- Calendar reads and contact lookup run without confirmation.
- Draft saving is reversible and confirmation is configurable.
- Email sending and calendar create/update require confirmation.
- Calendar cancellation is destructive and requires explicit confirmation.
- The approved proposal is hashed and recorded before execution.
- In SQLite mode, a repeated identical approved payload is not executed again after a
  process restart.
- Success is reported only when the provider returns a verified result.
- Long-term memory writes require an explicit user request in V0.1.

Credentials, tokens, local databases, and `.env` are ignored by Git. Google mode is
lazy: starting Jarvis does not authorize an account, and API access occurs only when a
specialist needs it.

## Project map

```text
jarvis/core/          LangGraph, state, routing, permissions, execution
jarvis/agents/        Typed email and calendar specialists
jarvis/integrations/  Mock and OAuth-backed Google providers
jarvis/memory/        Explicit long-term policy; checkpoints for short-term context
jarvis/voice/         Lazy push-to-talk STT and Kokoro TTS adapters
tests/                Behavioral and safety coverage
```

Voice remains optional because its local model packages are large. Install with
`python -m pip install -e ".[voice]"`, then run `python main.py --voice`. The same
graph, permissions, OAuth providers, and persistent thread are used in either mode.
The first voice launch downloads the selected faster-whisper and Kokoro model assets;
subsequent launches reuse the local model cache.

The default voice profile favors responsiveness: `base.en`, greedy Whisper decoding,
low Gemini thinking, concise spoken answers, and no separate model-routing request for
ordinary conversation. Set `JARVIS_STT_MODEL=small` if recognition accuracy matters
more than transcription speed. Add uncommon contact names to the comma-separated
`JARVIS_STT_HOTWORDS` setting to improve name recognition.
