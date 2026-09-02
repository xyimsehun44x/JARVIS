from __future__ import annotations

from datetime import date, timedelta

from jarvis import Jarvis


def test_schedule_read_then_contextual_move(jarvis: Jarvis) -> None:
    result = jarvis.turn("What's tomorrow looking like?", thread_id="calendar")
    assert "3 events" in result.response
    assert "Dinner with Minho" in result.response

    result = jarvis.turn("Move the dinner to Friday", thread_id="calendar")
    assert result.interrupt_kind == "clarification"
    assert "Which would you prefer" in result.prompt

    result = jarvis.resume("five", thread_id="calendar")
    assert result.interrupt_kind == "approval"
    assert "Dinner with Minho" in result.prompt
    assert "05:00 PM" in result.prompt

    result = jarvis.resume("yes", thread_id="calendar")
    assert "has been updated" in result.response
    dinner = next(event for event in jarvis.calendar.events if event.event_id == "event-dinner")
    assert dinner.start.hour == 17
    assert dinner.start.weekday() == 4


def test_create_event_requires_exact_approval(jarvis: Jarvis) -> None:
    before = len(jarvis.calendar.events)
    result = jarvis.turn("Add lunch with Jisoo tomorrow at 1 pm", thread_id="create")

    assert result.interrupt_kind == "approval"
    assert "Lunch With Jisoo" in result.prompt
    assert "01:00 PM" in result.prompt
    assert len(jarvis.calendar.events) == before

    result = jarvis.resume("yes", thread_id="create")
    assert "added to your calendar" in result.response
    assert len(jarvis.calendar.events) == before + 1


def test_cancel_rejection_leaves_event_untouched(jarvis: Jarvis) -> None:
    result = jarvis.turn("Cancel the dinner", thread_id="cancel")
    assert result.interrupt_kind == "approval"

    result = jarvis.resume("no", thread_id="cancel")
    assert "haven’t changed anything" in result.response
    assert any(event.event_id == "event-dinner" for event in jarvis.calendar.events)


def test_read_only_free_time_needs_no_approval(jarvis: Jarvis) -> None:
    result = jarvis.turn("When am I free next week?", thread_id="free")
    assert not result.needs_input
    assert "You’re free" in result.response

