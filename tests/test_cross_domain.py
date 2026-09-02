from jarvis import Jarvis


def test_calendar_to_email_handoff(jarvis: Jarvis) -> None:
    result = jarvis.turn(
        "Find when I'm free next week and email David Kim a couple of options.",
        thread_id="cross",
    )

    assert result.interrupt_kind == "approval"
    assert "Meeting availability" in result.prompt
    assert "david.kim@example.com" in result.prompt
    assert result.prompt.count("- ") == 2

    result = jarvis.resume("send it", thread_id="cross")
    assert result.response == "Sent."
    assert len(jarvis.gmail.sent) == 1


def test_cross_domain_ambiguous_contact_clarifies_before_proposal(jarvis: Jarvis) -> None:
    result = jarvis.turn(
        "Find when I'm free next week and email David some options.", thread_id="cross-ambiguous"
    )
    assert result.interrupt_kind == "clarification"
    assert "David Kim" in result.prompt and "David Lee" in result.prompt

    result = jarvis.resume("David Lee", thread_id="cross-ambiguous")
    assert result.interrupt_kind == "approval"
    assert "david.lee@example.com" in result.prompt


def test_calendar_approval_can_continue_into_email_edit_loop(jarvis: Jarvis) -> None:
    result = jarvis.turn(
        "Move the meeting with David to Thursday afternoon", thread_id="calendar-email"
    )
    assert result.interrupt_kind == "clarification"

    result = jarvis.resume("three", thread_id="calendar-email")
    assert result.interrupt_kind == "approval"
    assert "Meeting with David Kim" in result.prompt

    result = jarvis.resume(
        "Do it. And send him a quick apology.", thread_id="calendar-email"
    )
    assert result.interrupt_kind == "approval"
    assert "david.kim@example.com" in result.prompt
    assert "apologize" in result.prompt

    result = jarvis.resume("Make it less stiff", thread_id="calendar-email")
    assert result.interrupt_kind == "approval"

    result = jarvis.resume("Perfect. Send it.", thread_id="calendar-email")
    assert "has been updated, and the email has been sent" in result.response
    assert len(jarvis.gmail.sent) == 1
