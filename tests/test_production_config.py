from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agents.email.agent import EmailAgent
from jarvis.agents.email.schemas import EmailProposal, EmailRequest, ResolvedContact
from jarvis.config import Settings
from jarvis.core.brain import (
    EmailLanguageResult,
    GeminiConversationModel,
    OpenAIConversationModel,
    TurnClassification,
)
from jarvis.integrations.contacts import StaticContactProvider


class FakeResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(output_text="A properly model-generated response.")

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        if kwargs["text_format"] is TurnClassification:
            parsed = TurnClassification(route="calendar")
        else:
            parsed = EmailLanguageResult(subject="Model subject", body="Model body")
        return SimpleNamespace(output_parsed=parsed)


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class FakeInteractions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response_format = kwargs.get("response_format", {})
        schema = response_format.get("schema", {})
        if "route" in schema.get("properties", {}):
            output = '{"route":"calendar"}'
        elif response_format:
            output = '{"subject":"Gemini subject","body":"Gemini body"}'
        else:
            output = "A Gemini-generated response."
        return SimpleNamespace(output_text=output)


class FakeGeminiClient:
    def __init__(self) -> None:
        self.interactions = FakeInteractions()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_openai_model_uses_responses_for_conversation_routing_and_email() -> None:
    client = FakeClient()
    model = OpenAIConversationModel(model="test-model", api_key="test", client=client)

    assert "model-generated" in model.respond("Hello", [])
    assert model.classify("Move lunch", []) == "calendar"

    agent = EmailAgent(StaticContactProvider(), language_model=model)
    contact = ResolvedContact(name="Jisoo Park", email="jisoo@example.com")
    proposal = agent.compose(
        EmailRequest(operation="send", desired_outcome="confirm lunch"), contact
    )
    assert proposal.subject == "Model subject"
    assert proposal.body == "Model body"
    assert proposal.recipient_email == "jisoo@example.com"
    assert proposal.operation == "send"


def test_gemini_model_uses_native_interactions_for_all_language_tasks() -> None:
    client = FakeGeminiClient()
    model = GeminiConversationModel(
        model="gemini-3.8-flash",
        api_key="test",
        thinking_level="medium",
        client=client,
    )

    assert "Gemini-generated" in model.respond("Hello", [])
    assert model.classify("Move lunch", []) == "calendar"

    agent = EmailAgent(StaticContactProvider(), language_model=model)
    contact = ResolvedContact(name="Jisoo Park", email="jisoo@example.com")
    proposal = agent.compose(
        EmailRequest(operation="send", desired_outcome="confirm lunch"), contact
    )
    revised = agent.revise(
        EmailProposal(
            recipient_name=contact.name,
            recipient_email=contact.email,
            subject="Original",
            body="Original body",
            operation="send",
        ),
        "Make it friendlier",
    )

    assert proposal.subject == "Gemini subject"
    assert revised.body == "Gemini body"
    assert all(call["model"] == "gemini-3.8-flash" for call in client.interactions.calls)
    assert client.interactions.calls[0]["generation_config"]["thinking_level"] == "medium"
    assert "at most 30 words" not in client.interactions.calls[0]["system_instruction"]
    assert all(
        call["generation_config"]["thinking_level"] == "low"
        for call in client.interactions.calls[1:]
    )
    model.close()
    assert client.closed


def test_gemini_model_yields_only_streamed_text_deltas() -> None:
    client = FakeGeminiClient()
    events = [
        SimpleNamespace(event_type="interaction.created"),
        SimpleNamespace(
            event_type="step.delta",
            delta=SimpleNamespace(type="text", text="Good "),
        ),
        SimpleNamespace(
            event_type="step.delta",
            delta=SimpleNamespace(type="thought_summary", text="hidden"),
        ),
        SimpleNamespace(
            event_type="step.delta",
            delta=SimpleNamespace(type="text", text="morning."),
        ),
        SimpleNamespace(
            event_type="interaction.completed",
            interaction=SimpleNamespace(status="completed"),
        ),
    ]
    client.interactions.create = lambda **kwargs: (
        client.interactions.calls.append(kwargs) or iter(events)
    )
    model = GeminiConversationModel(model="gemini-test", api_key="test", client=client)

    assert list(model.respond_stream("Hello", [])) == ["Good ", "morning."]
    assert client.interactions.calls[0]["stream"] is True
    assert client.interactions.calls[0]["store"] is False
    assert client.interactions.calls[0]["generation_config"]["max_output_tokens"] == 320
    assert client.interactions.calls[0]["generation_config"]["thinking_level"] == "low"
    assert "at most 30 words" in client.interactions.calls[0]["system_instruction"]


def test_gemini_text_stream_uses_normal_text_response_policy() -> None:
    client = FakeGeminiClient()
    events = [
        SimpleNamespace(
            event_type="step.delta",
            delta=SimpleNamespace(type="text", text="A desktop response."),
        ),
        SimpleNamespace(
            event_type="interaction.completed",
            interaction=SimpleNamespace(status="completed"),
        ),
    ]
    client.interactions.create = lambda **kwargs: (
        client.interactions.calls.append(kwargs) or iter(events)
    )
    model = GeminiConversationModel(model="gemini-test", api_key="test", client=client)

    assert list(model.respond_stream("Hello", [], voice_mode=False)) == [
        "A desktop response."
    ]
    call = client.interactions.calls[0]
    assert call["generation_config"]["max_output_tokens"] == 160
    assert call["generation_config"]["thinking_level"] == "low"
    assert "at most 30 words" not in call["system_instruction"]


def test_gemini_stream_requires_explicit_completion_event() -> None:
    client = FakeGeminiClient()
    client.interactions.create = lambda **kwargs: iter(
        [
            SimpleNamespace(
                event_type="step.delta",
                delta=SimpleNamespace(type="text", text="Partial response."),
            )
        ]
    )
    model = GeminiConversationModel(model="gemini-test", api_key="test", client=client)

    with pytest.raises(RuntimeError, match="ended before completion"):
        list(model.respond_stream("Hello", []))


def test_gemini_stream_rejects_incomplete_terminal_status() -> None:
    client = FakeGeminiClient()
    client.interactions.create = lambda **kwargs: iter(
        [
            SimpleNamespace(
                event_type="step.delta",
                delta=SimpleNamespace(type="text", text="A complete sentence."),
            ),
            SimpleNamespace(
                event_type="interaction.completed",
                interaction=SimpleNamespace(status="incomplete"),
            ),
        ]
    )
    model = GeminiConversationModel(model="gemini-test", api_key="test", client=client)
    stream = model.respond_stream("Hello", [])

    assert next(stream) == "A complete sentence."
    with pytest.raises(RuntimeError, match="status incomplete"):
        next(stream)


def test_gemini_stream_recovers_missing_suffix_from_completed_output() -> None:
    client = FakeGeminiClient()
    final_text = "Sharks have inhabited the oceans for roughly 450 million years."
    events = [
        SimpleNamespace(
            event_type="step.delta",
            delta=SimpleNamespace(
                type="text", text="Sharks have inhabited the oceans for roughly"
            ),
        ),
        SimpleNamespace(
            event_type="interaction.completed",
            interaction=SimpleNamespace(
                status="completed",
                steps=[
                    SimpleNamespace(
                        type="model_output",
                        content=[SimpleNamespace(type="text", text=final_text)],
                    )
                ],
            ),
        ),
    ]
    client.interactions.create = lambda **kwargs: (
        client.interactions.calls.append(kwargs) or iter(events)
    )
    model = GeminiConversationModel(model="gemini-test", api_key="test", client=client)

    chunks = list(model.respond_stream("Tell me something interesting", []))

    assert chunks == [
        "Sharks have inhabited the oceans for roughly",
        " 450 million years.",
    ]
    assert "".join(chunks) == final_text


def test_google_configuration_reports_missing_secrets(tmp_path) -> None:
    settings = Settings(
        mode="google",
        llm_provider="openai",
        openai_api_key=None,
        google_credentials_path=str(tmp_path / "credentials.json"),
    )
    errors = settings.configuration_errors()
    assert any("OPENAI_API_KEY" in error for error in errors)
    assert any("OAuth credentials" in error for error in errors)


def test_gemini_configuration_reports_missing_api_key() -> None:
    settings = Settings(llm_provider="gemini", gemini_api_key=None)

    assert any("GEMINI_API_KEY" in error for error in settings.configuration_errors())


def test_weather_configuration_loads_location_and_validates_timeout(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DEFAULT_LOCATION", "Seoul, South Korea")
    monkeypatch.setenv("JARVIS_WEATHER_TIMEOUT_SECONDS", "2.5")

    settings = Settings.from_env()

    assert settings.default_location == "Seoul, South Korea"
    assert settings.weather_timeout_seconds == 2.5
    assert any(
        "WEATHER_TIMEOUT" in error
        for error in Settings(weather_timeout_seconds=0).configuration_errors()
    )


def test_accuracy_first_stt_defaults() -> None:
    settings = Settings()

    assert settings.stt_model == "small.en"
    assert settings.stt_beam_size == 5
    assert settings.stt_vad_filter is True
    assert settings.stt_action_confidence_threshold == 0.35
    assert settings.stt_vocabulary_limit == 15
    assert settings.vocabulary_path == "jarvis.vocabulary.json"
    assert "calendar" in settings.stt_initial_prompt
    assert "Joo" in settings.stt_initial_prompt
    assert "Tell me something interesting" in settings.stt_initial_prompt


def test_minimal_gemini_thinking_level_is_valid() -> None:
    assert Settings(gemini_thinking_level="minimal").configuration_errors() == []


def test_stt_action_confidence_threshold_is_validated(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_STT_ACTION_CONFIDENCE_THRESHOLD", "0.25")

    assert Settings.from_env().stt_action_confidence_threshold == 0.25
    assert any(
        "STT_ACTION_CONFIDENCE_THRESHOLD" in error
        for error in Settings(stt_action_confidence_threshold=1.1).configuration_errors()
    )
    assert any(
        "STT_VOCABULARY_LIMIT" in error
        for error in Settings(stt_vocabulary_limit=16).configuration_errors()
    )
