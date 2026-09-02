from jarvis import Jarvis


def test_conversation_is_first_class_and_persistent(jarvis: Jarvis) -> None:
    first = jarvis.turn("Jarvis, tell me something interesting.", thread_id="chat")
    second = jarvis.turn("Thanks", thread_id="chat")

    assert not first.needs_input
    assert "Octopuses" in first.response
    assert second.response == "Of course."
    messages = jarvis.state(thread_id="chat")["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_threads_do_not_share_conversation_history(jarvis: Jarvis) -> None:
    jarvis.turn("Hello", thread_id="one")
    jarvis.turn("Thanks", thread_id="two")

    assert len(jarvis.state(thread_id="one")["messages"]) == 2
    assert len(jarvis.state(thread_id="two")["messages"]) == 2

