from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.integrations.weather import (
    MockWeatherProvider,
    WeatherProviderError,
    WeatherTimeoutError,
)


EVAL_DIRECTORY = Path(__file__).parent / "evals"
EVAL_FILES = (
    "conversations.jsonl",
    "email_cases.jsonl",
    "calendar_cases.jsonl",
    "weather_cases.jsonl",
)


def load_eval_cases() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for filename in EVAL_FILES:
        path = EVAL_DIRECTORY / filename
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Invalid JSON in {filename}:{line_number}: {exc}") from exc
            missing = {"id", "input", "route", "expect"} - case.keys()
            if missing:
                raise AssertionError(
                    f"{filename}:{line_number} is missing required fields: {sorted(missing)}"
                )
            if case["id"] in seen_ids:
                raise AssertionError(f"Duplicate eval id: {case['id']}")
            seen_ids.add(case["id"])
            cases.append((filename, case))
    return cases


EVAL_CASES = load_eval_cases()


def _assert_turn(result, expected: dict[str, Any], *, case_id: str) -> None:
    if "needs_input" in expected:
        assert result.needs_input is expected["needs_input"], case_id
    if "interrupt" in expected:
        assert result.interrupt_kind == expected["interrupt"], case_id
    for fragment in expected.get("prompt_contains", []):
        assert fragment.casefold() in (result.prompt or "").casefold(), case_id
    for fragment in expected.get("response_contains", []):
        assert fragment.casefold() in (result.response or "").casefold(), case_id
    if expected.get("proposal_absent"):
        assert result.proposal is None, case_id
    if "proposal_operation" in expected:
        assert result.proposal is not None, case_id
        assert result.proposal["payload"]["operation"] == expected["proposal_operation"], case_id


def _calendar_fingerprint(jarvis: Jarvis) -> list[tuple[Any, ...]]:
    return sorted(
        (
            event.event_id,
            event.title,
            event.start.isoformat(),
            event.end.isoformat(),
            tuple(event.attendees),
        )
        for event in jarvis.calendar.events
    )


def _assert_final(
    jarvis: Jarvis,
    thread_id: str,
    expected: dict[str, Any],
    initial_calendar: list[tuple[Any, ...]],
    *,
    case_id: str,
) -> None:
    if "sent" in expected:
        assert len(jarvis.gmail.sent) == expected["sent"], case_id
    if "drafts" in expected:
        assert len(jarvis.gmail.drafts) == expected["drafts"], case_id
    if "calendar_changed" in expected:
        changed = _calendar_fingerprint(jarvis) != initial_calendar
        assert changed is expected["calendar_changed"], case_id
    if "event_present" in expected:
        event_id, should_exist = expected["event_present"]
        exists = any(event.event_id == event_id for event in jarvis.calendar.events)
        assert exists is should_exist, case_id
    if "execution_ok" in expected:
        execution = jarvis.state(thread_id=thread_id).get("execution_result") or {}
        assert execution.get("ok") is expected["execution_ok"], case_id


@pytest.mark.parametrize(
    ("suite", "case"),
    EVAL_CASES,
    ids=[f"{suite.removesuffix('.jsonl')}::{case['id']}" for suite, case in EVAL_CASES],
)
def test_text_eval_case(jarvis: Jarvis, suite: str, case: dict[str, Any]) -> None:
    del suite
    case_id = case["id"]
    thread_id = f"eval::{case_id}"
    setup = case.get("setup", {})
    owns_jarvis = bool(setup)
    if setup:
        weather_error = None
        if setup.get("weather_error") == "timeout":
            weather_error = WeatherTimeoutError("simulated timeout")
        elif setup.get("weather_error") == "failure":
            weather_error = WeatherProviderError("simulated failure")
        jarvis = Jarvis(
            settings=Settings(default_location=setup.get("default_location")),
            weather=MockWeatherProvider(error=weather_error),
        )
    initial_calendar = _calendar_fingerprint(jarvis)
    try:
        assert jarvis.select_route(case["input"]).value == case["route"], case_id
        result = jarvis.turn(case["input"], thread_id=thread_id)
        _assert_turn(result, case["expect"], case_id=case_id)

        for followup in case.get("followups", []):
            if result.needs_input:
                result = jarvis.resume(followup["input"], thread_id=thread_id)
            else:
                result = jarvis.turn(followup["input"], thread_id=thread_id)
            _assert_turn(result, followup["expect"], case_id=case_id)

        _assert_final(
            jarvis,
            thread_id,
            case.get("final", {}),
            initial_calendar,
            case_id=case_id,
        )
    finally:
        if owns_jarvis:
            jarvis.close()
