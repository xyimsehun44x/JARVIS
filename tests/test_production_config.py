from __future__ import annotations

from types import SimpleNamespace

from jarvis.agents.email.agent import EmailAgent
from jarvis.agents.email.schemas import EmailRequest, ResolvedContact
from jarvis.config import Settings
from jarvis.core.brain import EmailLanguageResult, OpenAIConversationModel, TurnClassification
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

