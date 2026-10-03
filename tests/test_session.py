from __future__ import annotations

import pytest

from jarvis import Jarvis
from jarvis.config import Settings
from jarvis.core.brain import EmailLanguageResult
from jarvis.core.jarvis import INCOMPLETE_VOICE_RESPONSE
from jarvis.core.latency import LatencyRecorder
from jarvis.core.session import ExecutionTier, SessionCoordinator, TierDispatcher
from jarvis.voice.normalizer import NormalizedUtterance


class RecordingConversationModel:
    def __init__(self) -> None:
        self.calls = []

    def respond(self, text, history):
        self.calls.append((text, list(history)))
        return f"Reply to: {text}"


class StreamingConversationModel(RecordingConversationModel):
    def __init__(
        self, *, fail_before_text: bool = False, fail_after_text: bool = False
    ) -> None:
        super().__init__()
        self.fail_before_text = fail_before_text
        self.fail_after_text = fail_after_text
        self.stream_calls = []

    def respond_stream(self, text, history, *, voice_mode=True):
        del voice_mode
        self.stream_calls.append((text, list(history)))
        if self.fail_before_text:
            raise RuntimeError("stream unavailable")
        yield "A streamed "
        if self.fail_after_text:
            raise RuntimeError("stream interrupted")
        yield "reply."


class ChangingEmailModel(RecordingConversationModel):
    def __init__(self, label: str = "draft") -> None:
        super().__init__()
        self.label = label
        self.draft_calls = 0

    def draft_email(self, request, contact):
        del request, contact
        self.draft_calls += 1
        return EmailLanguageResult(
            subject=f"{self.label}-{self.draft_calls}",
            body=f"Body {self.label}-{self.draft_calls}",
        )


def test_tier_dispatcher_is_conservative_about_actions_and_fresh_data() -> None:
    jarvis = Jarvis(settings=Settings())
    dispatcher = TierDispatcher(jarvis)

    assert dispatcher.decide(
        "Hello, Jarvis", has_pending_input=False, pending_known_locally=False
    ).tier is ExecutionTier.CONVERSATION
    assert dispatcher.decide(
        "Email Jisoo", has_pending_input=False, pending_known_locally=False
    ).tier is ExecutionTier.AGENT
    assert dispatcher.decide(
        "How is the weather today?", has_pending_input=False, pending_known_locally=False
    ).tier is ExecutionTier.AGENT
    assert dispatcher.decide(
        "Could you handle something for me?",
        has_pending_input=False,
        pending_known_locally=False,
    ).tier is ExecutionTier.AGENT
    jarvis.close()


def test_tier_one_conversation_persists_in_shared_graph_thread() -> None:
    model = RecordingConversationModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    first = coordinator.turn("Hello", thread_id="shared")
    second = coordinator.turn("Thanks", thread_id="shared")

    assert first.execution_tier == 1
    assert second.execution_tier == 1
    assert model.calls[0][1] == []
    assert [message["content"] for message in model.calls[1][1]] == [
        "Hello",
        "Reply to: Hello",
    ]
    persisted = jarvis.state(thread_id="shared")["messages"]
    assert [message["content"] for message in persisted] == [
        "Hello",
        "Reply to: Hello",
        "Thanks",
        "Reply to: Thanks",
    ]
    jarvis.close()


def test_tier_one_streams_and_persists_the_exact_assembled_response() -> None:
    recorder = LatencyRecorder(enabled=True)
    model = StreamingConversationModel()
    jarvis = Jarvis(
        settings=Settings(), conversation_model=model, latency_recorder=recorder
    )
    coordinator = SessionCoordinator(jarvis)
    deltas = []

    result = coordinator.turn(
        "Hello",
        thread_id="streamed",
        trace_id="stream-trace",
        on_response_delta=deltas.append,
    )

    assert deltas == ["A streamed ", "reply."]
    assert result.response == "A streamed reply."
    assert result.response_streamed is True
    assert [message["content"] for message in result.state["messages"]] == [
        "Hello",
        "A streamed reply.",
    ]
    assert "llm.first_token" in {
        event.stage for event in recorder.events_since()
    }
    assert model.calls == []
    jarvis.close()


def test_tier_one_falls_back_before_any_streamed_text() -> None:
    recorder = LatencyRecorder(enabled=True)
    model = StreamingConversationModel(fail_before_text=True)
    jarvis = Jarvis(
        settings=Settings(), conversation_model=model, latency_recorder=recorder
    )
    deltas = []

    result = SessionCoordinator(jarvis).turn(
        "Hello", on_response_delta=deltas.append
    )

    assert result.response == "Reply to: Hello"
    assert result.response_streamed is False
    assert deltas == []
    assert len(model.calls) == 1
    assert "llm.stream_fallback" in {
        event.stage for event in recorder.events_since()
    }
    jarvis.close()


def test_tier_one_never_duplicates_a_partially_streamed_response() -> None:
    recorder = LatencyRecorder(enabled=True)
    model = StreamingConversationModel(fail_after_text=True)
    jarvis = Jarvis(
        settings=Settings(), conversation_model=model, latency_recorder=recorder
    )
    deltas = []

    result = SessionCoordinator(jarvis).turn(
        "Hello", on_response_delta=deltas.append
    )

    assert result.response == "A streamed"
    assert result.response_streamed is True
    assert deltas == ["A streamed "]
    assert model.calls == []
    assert result.state["messages"][-1]["content"] == "A streamed"
    assert "llm.stream_interrupted" in {
        event.stage for event in recorder.events_since()
    }
    jarvis.close()


def test_tier_one_trims_and_persists_only_the_last_complete_sentence() -> None:
    class DanglingConversationModel(RecordingConversationModel):
        def respond_stream(self, text, history, *, voice_mode=True):
            del voice_mode
            del text, history
            yield "I am sorry to hear that. "
            yield "Would"

    recorder = LatencyRecorder(enabled=True)
    model = DanglingConversationModel()
    jarvis = Jarvis(
        settings=Settings(), conversation_model=model, latency_recorder=recorder
    )

    result = SessionCoordinator(jarvis).turn(
        "I had a difficult interview", on_response_delta=lambda chunk: None
    )

    assert result.response == "I am sorry to hear that."
    assert result.state["messages"][-1]["content"] == "I am sorry to hear that."
    assert "llm.incomplete_tail_trimmed" in {
        event.stage for event in recorder.events_since()
    }
    assert model.calls == []
    jarvis.close()


def test_tier_one_rejects_a_voice_stream_with_no_complete_sentence() -> None:
    class IncompleteVoiceModel(RecordingConversationModel):
        def respond_stream(self, text, history, *, voice_mode=True):
            assert voice_mode is True
            del text, history
            yield "Remarkably, sharks have inhabited our oceans for roughly"

    recorder = LatencyRecorder(enabled=True)
    jarvis = Jarvis(
        settings=Settings(),
        conversation_model=IncompleteVoiceModel(),
        latency_recorder=recorder,
    )
    deltas = []

    result = SessionCoordinator(jarvis).turn(
        "Tell me something interesting",
        on_response_delta=deltas.append,
        voice_response=True,
    )

    assert deltas == ["Remarkably, sharks have inhabited our oceans for roughly"]
    assert result.response == INCOMPLETE_VOICE_RESPONSE
    assert result.response_streamed is False
    assert result.state["messages"][-1]["content"] == INCOMPLETE_VOICE_RESPONSE
    assert "llm.incomplete_response_rejected" in {
        event.stage for event in recorder.events_since()
    }
    jarvis.close()


def test_tier_one_conversation_survives_process_restart(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    settings = Settings(persistence="sqlite", database_path=database)
    first_model = RecordingConversationModel()
    first = Jarvis(settings=settings, conversation_model=first_model)
    SessionCoordinator(first).turn("Hello", thread_id="durable-tier-one")
    first.close()

    second_model = RecordingConversationModel()
    second = Jarvis(settings=settings, conversation_model=second_model)
    result = SessionCoordinator(second).turn("Thanks", thread_id="durable-tier-one")

    assert result.execution_tier == 1
    assert [message["content"] for message in second_model.calls[0][1]] == [
        "Hello",
        "Reply to: Hello",
    ]
    second.close()


def test_explicit_control_of_locally_pending_task_uses_tier_zero() -> None:
    model = ChangingEmailModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    proposed = coordinator.turn("Email Jisoo and say hello", thread_id="approval")
    sent = coordinator.turn("send it", thread_id="approval")

    assert proposed.execution_tier == 2
    assert proposed.needs_input
    assert sent.execution_tier == 0
    assert sent.response == "Sent."
    assert model.draft_calls == 1
    assert len(jarvis.gmail.sent) == 1
    assert jarvis.gmail.sent[0].subject == "draft-1"
    jarvis.close()


def test_unknown_persisted_interrupt_falls_through_to_tier_two() -> None:
    jarvis = Jarvis(settings=Settings())
    first_coordinator = SessionCoordinator(jarvis)
    first_coordinator.turn("Email Jisoo and say hello", thread_id="restored")

    restored_coordinator = SessionCoordinator(jarvis)
    result = restored_coordinator.turn("send it", thread_id="restored")

    assert result.execution_tier == 2
    assert result.response == "Sent."
    jarvis.close()


def test_exact_email_proposal_survives_restart_without_redrafting(tmp_path) -> None:
    database = str(tmp_path / "jarvis.db")
    settings = Settings(persistence="sqlite", database_path=database)
    first_model = ChangingEmailModel("original")
    first = Jarvis(settings=settings, conversation_model=first_model)
    SessionCoordinator(first).turn("Email Jisoo and say hello", thread_id="durable-proposal")
    assert first_model.draft_calls == 1
    first.close()

    second_model = ChangingEmailModel("replacement")
    second = Jarvis(settings=settings, conversation_model=second_model)
    result = SessionCoordinator(second).turn("send it", thread_id="durable-proposal")

    assert result.response == "Sent."
    assert result.execution_tier == 2
    assert second_model.draft_calls == 0
    assert second.gmail.sent[0].subject == "original-1"
    second.close()


def test_direct_conversation_cannot_bypass_pending_task() -> None:
    jarvis = Jarvis(settings=Settings())
    jarvis.turn("Email Jisoo and say hello", thread_id="pending")

    with pytest.raises(RuntimeError, match="waiting for user input"):
        jarvis.direct_conversation("Never mind", thread_id="pending")
    jarvis.close()


def test_clear_new_request_abandons_contact_clarification() -> None:
    model = RecordingConversationModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    pending = coordinator.turn("Email Nobody and say hello", thread_id="topic-switch")
    result = coordinator.turn(
        "Tell me one useful fact about sleep.", thread_id="topic-switch"
    )

    assert pending.interrupt_kind == "clarification"
    assert result.execution_tier == 1
    assert result.response == "Reply to: Tell me one useful fact about sleep."
    assert jarvis.gmail.sent == []
    assert not jarvis.has_pending_input("topic-switch")
    jarvis.close()


def test_clear_new_request_does_not_replay_failed_external_reads_after_restart(
    tmp_path,
) -> None:
    class ContactProviderThatFailsIfReplayed:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str):
            del query
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("expired external authorization")
            return []

    contacts = ContactProviderThatFailsIfReplayed()
    settings = Settings(
        persistence="sqlite",
        database_path=str(tmp_path / "pending-abandon.db"),
    )
    first = Jarvis(
        settings=settings,
        contacts=contacts,
        conversation_model=RecordingConversationModel(),
    )
    pending = SessionCoordinator(first).turn(
        "Email Nobody and say hello", thread_id="persisted-topic-switch"
    )
    assert pending.needs_input
    first.close()

    second = Jarvis(
        settings=settings,
        contacts=contacts,
        conversation_model=RecordingConversationModel(),
    )
    result = SessionCoordinator(second).turn(
        "Remember that my timezone is Asia/Seoul",
        thread_id="persisted-topic-switch",
    )

    assert "Asia/Seoul" in (result.response or "")
    assert contacts.calls == 1
    assert not second.has_pending_input("persisted-topic-switch")
    second.close()


def test_clear_new_request_abandons_proposal_without_sending() -> None:
    model = ChangingEmailModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    pending = coordinator.turn("Email Jisoo and say hello", thread_id="proposal-switch")
    result = coordinator.turn("What is a useful sleep fact?", thread_id="proposal-switch")

    assert pending.needs_input
    assert result.execution_tier == 1
    assert jarvis.gmail.sent == []
    assert not jarvis.has_pending_input("proposal-switch")
    jarvis.close()


def test_invalid_speech_is_rejected_before_routing() -> None:
    model = RecordingConversationModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)
    utterance = NormalizedUtterance(
        raw_transcript="",
        normalized_text="",
        stt_confidence=0.0,
        requires_clarification=True,
        rejection_reason="silence",
    )

    result = coordinator.turn(utterance, thread_id="invalid-speech")

    assert result.needs_input
    assert result.interrupt_kind == "speech_clarification"
    assert result.execution_tier == 0
    assert model.calls == []
    assert jarvis.state(thread_id="invalid-speech") == {}
    jarvis.close()


def test_low_confidence_conversation_proceeds_but_action_repeats() -> None:
    model = RecordingConversationModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)
    coordinator = SessionCoordinator(jarvis)

    conversation = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="Tell me a joke",
            normalized_text="Tell me a joke",
            stt_confidence=0.2,
        ),
        thread_id="low-conversation",
    )
    action = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="Email Jisoo and say hello",
            normalized_text="Email Jisoo and say hello",
            stt_confidence=0.2,
        ),
        thread_id="low-action",
    )

    assert conversation.execution_tier == 1
    assert action.interrupt_kind == "speech_clarification"
    assert jarvis.gmail.sent == []
    jarvis.close()


def test_low_confidence_pending_approval_cannot_execute() -> None:
    jarvis = Jarvis(settings=Settings())
    coordinator = SessionCoordinator(jarvis)
    pending = coordinator.turn("Email Jisoo and say hello", thread_id="uncertain-approval")

    uncertain = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="send it",
            normalized_text="send it",
            stt_confidence=0.1,
        ),
        thread_id="uncertain-approval",
    )

    assert pending.needs_input
    assert uncertain.interrupt_kind == "speech_clarification"
    assert jarvis.gmail.sent == []
    assert jarvis.has_pending_input("uncertain-approval")

    sent = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="send it",
            normalized_text="send it",
            stt_confidence=0.9,
        ),
        thread_id="uncertain-approval",
    )

    assert sent.response == "Sent."
    assert len(jarvis.gmail.sent) == 1
    jarvis.close()


def test_normalized_utterance_preserves_weather_followup_routing() -> None:
    jarvis = Jarvis(settings=Settings())
    coordinator = SessionCoordinator(jarvis)

    first = coordinator.turn(
        "What's the weather in Seoul today?", thread_id="voice-weather"
    )
    result = coordinator.turn(
        NormalizedUtterance(
            raw_transcript="What about tomorrow?",
            normalized_text="What about tomorrow?",
            stt_confidence=0.8,
        ),
        thread_id="voice-weather",
    )

    assert result.execution_tier == 2
    assert result.tier_reason == "weather context continuation"
    assert result.state["weather_context"]["target_date"] > first.state[
        "weather_context"
    ]["target_date"]
    jarvis.close()


def test_coordinator_records_tier_latency() -> None:
    recorder = LatencyRecorder(enabled=True)
    jarvis = Jarvis(settings=Settings(), latency_recorder=recorder)
    coordinator = SessionCoordinator(jarvis)

    coordinator.turn("Hello", thread_id="timed", trace_id="session-trace")
    jarvis.close()

    stages = {event.stage for event in recorder.events_since()}
    assert {"tier.dispatch", "tier.1.total", "turn.total", "llm.respond"} <= stages
    assert all(event.trace_id == "session-trace" for event in recorder.events_since())
