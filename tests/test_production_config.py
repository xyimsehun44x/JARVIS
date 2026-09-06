from __future__ import annotations

from types import SimpleNamespace

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
    assert all(
        call["generation_config"]["thinking_level"] == "low"
        for call in client.interactions.calls[1:]
    )
    model.close()
    assert client.closed


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
