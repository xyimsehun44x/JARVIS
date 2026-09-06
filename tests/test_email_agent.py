from jarvis import Jarvis
from jarvis.agents.email.schemas import ResolvedContact
from jarvis.config import Settings
from jarvis.integrations.contacts import StaticContactProvider


def test_ambiguous_contact_edit_loop_and_verified_send(jarvis: Jarvis) -> None:
    result = jarvis.turn(
        "Email David and tell him I need to move next week's meeting for family reasons.",
        thread_id="email-edit",
    )
    assert result.needs_input and result.interrupt_kind == "clarification"
    assert "David Kim" in result.prompt and "David Lee" in result.prompt

    result = jarvis.resume("David Kim", thread_id="email-edit")
    assert result.interrupt_kind == "approval"
    assert "david.kim@example.com" in result.prompt
    assert "family reasons" in result.prompt

    result = jarvis.resume("Make it less formal", thread_id="email-edit")
    assert result.needs_input and "Just a quick note" in result.prompt

    result = jarvis.resume("Actually don't mention family", thread_id="email-edit")
    assert result.needs_input and "family" not in result.prompt.lower()
    assert "Just a quick note" in result.prompt

    result = jarvis.resume("Send it", thread_id="email-edit")
    assert result.response == "Sent."
    assert len(jarvis.gmail.sent) == 1
    assert "family" not in jarvis.gmail.sent[0].body.lower()
    assert jarvis.state(thread_id="email-edit")["execution_result"]["ok"] is True


def test_rejection_performs_no_external_write(jarvis: Jarvis) -> None:
    result = jarvis.turn("Email Jisoo and say hello", thread_id="reject")
    assert result.interrupt_kind == "approval"

    result = jarvis.resume("Never mind", thread_id="reject")
    assert "haven’t sent anything" in result.response
    assert jarvis.gmail.sent == []


def test_draft_save_is_reversible_and_mocked(jarvis: Jarvis) -> None:
    result = jarvis.turn("Draft an email to Jisoo saying hello", thread_id="draft")

    assert result.response == "Draft saved."
    assert len(jarvis.gmail.drafts) == 1
    assert not result.needs_input


def test_lowercase_transcript_recipient_is_resolved(jarvis: Jarvis) -> None:
    result = jarvis.turn("email jisoo and say hello", thread_id="lowercase-recipient")
    assert result.interrupt_kind == "approval"
    assert "jisoo@example.com" in result.prompt


def test_identical_payload_in_separate_tasks_can_be_sent_intentionally(jarvis: Jarvis) -> None:
    for thread_id in ("first-send", "second-send"):
        result = jarvis.turn("Email Jisoo and say hello", thread_id=thread_id)
        assert result.needs_input
        result = jarvis.resume("send it", thread_id=thread_id)
        assert result.response == "Sent."

    assert len(jarvis.gmail.sent) == 2


def test_spoken_spelling_correction_is_used_as_contact_query() -> None:
    jarvis = Jarvis(
        settings=Settings(),
        contacts=StaticContactProvider(
            [ResolvedContact(name="Joo Kim", email="joo@example.com")]
        ),
    )
    result = jarvis.turn("Email June and say hello", thread_id="spelled-contact")
    assert result.interrupt_kind == "clarification"

    result = jarvis.resume("I meant say J-O-O.", thread_id="spelled-contact")

    assert result.interrupt_kind == "approval"
    assert "Joo Kim <joo@example.com>" in result.prompt
    jarvis.close()


def test_contact_correction_normalizes_natural_phrasing() -> None:
    assert Jarvis._contact_query_from_answer("I meant say J-O-O.") == "JOO"
    assert Jarvis._contact_query_from_answer("and then JOO") == "JOO"
    assert Jarvis._contact_query_from_answer("Jew, Jew.") == "Jew"
    assert (
        Jarvis._contact_query_from_answer("Actually use joo@example.com")
        == "joo@example.com"
    )
