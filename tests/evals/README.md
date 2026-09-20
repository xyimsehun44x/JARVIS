# Text evaluation cases

Each non-empty JSONL line describes one isolated Jarvis conversation. The pytest runner
loads every file in this directory and uses mock providers, so evaluations never contact
Gmail, Google Calendar, Open-Meteo, or a hosted language model.

Required fields:

- `id`: unique, stable scenario name
- `input`: initial user utterance
- `route`: expected `conversation`, `email`, `calendar`, `cross_domain`, `weather`, or
  `unsupported_fresh_data` route
- `expect`: assertions for the initial turn

Optional fields:

- `followups`: ordered `{ "input": ..., "expect": ... }` turns
- `final`: final mock-provider and execution-state assertions
- `setup`: optional deterministic settings such as `default_location` or a simulated
  `weather_error` (`timeout` or `failure`)

Supported turn assertions include `needs_input`, `interrupt`, `prompt_contains`,
`response_contains`, `proposal_absent`, and `proposal_operation`. Supported final
assertions include `sent`, `drafts`, `calendar_changed`, `event_present`, and
`execution_ok`.

Run only the text evaluations with:

```powershell
python -m pytest tests/test_evals.py -vv
```
