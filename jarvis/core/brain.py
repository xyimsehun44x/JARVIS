from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel

from jarvis.core.prompts import JARVIS_PERSONA


class ConversationModel(Protocol):
    """Replaceable boundary for a hosted or local conversational model."""

    def respond(self, text: str, history: list[dict[str, str]]) -> str: ...


class TurnClassification(BaseModel):
    route: Literal["conversation", "email", "calendar", "cross_domain"]


class EmailLanguageResult(BaseModel):
    subject: str
    body: str


class RuleBasedConversationModel:
    """Offline development model; domain actions never depend on these responses."""

    def respond(self, text: str, history: list[dict[str, str]]) -> str:
        lowered = text.lower().strip()
        if lowered in {"jarvis", "jarvis."}:
            return "At your service."
        if any(word in lowered for word in ("hello", "hi jarvis", "good morning")):
            return "Hello. What can I do for you?"
        if "thank" in lowered:
            return "Of course."
        if "interesting" in lowered:
            return (
                "Octopuses have three hearts, and two of them stop beating while they swim. "
                "Even nature occasionally objects to cardio."
            )
        if "hate monday meetings" in lowered:
            return "A defensible position. I won’t make it a permanent preference unless you ask me to."
        if history:
            return "I’m with you. What would you like to do next?"
        return "I’m listening."


class OpenAIConversationModel:
    """Production language boundary using the OpenAI Responses API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
        self.client = client
        self.model = model

    def respond(self, text: str, history: list[dict[str, str]]) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=JARVIS_PERSONA,
            input=[*history[-20:], {"role": "user", "content": text}],
            store=False,
        )
        output = response.output_text.strip()
        if not output:
            raise RuntimeError("The language model returned an empty response")
        return output

    def classify(self, text: str, history: list[dict[str, str]]) -> str:
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "Classify the user's latest turn for a personal assistant. Use conversation "
                "for ordinary dialogue, email for email-only work, calendar for calendar-only "
                "work, and cross_domain only when both calendar data and email are required."
            ),
            input=[*history[-8:], {"role": "user", "content": text}],
            text_format=TurnClassification,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The language model did not return a route")
        return parsed.route

    def draft_email(self, request: Any, contact: Any) -> EmailLanguageResult:
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "Draft a concise email. Do not invent facts, dates, reasons, promises, or a "
                "sender signature. Use only the supplied request and resolved contact."
            ),
            input=(
                f"Resolved recipient: {contact.model_dump_json()}\n"
                f"Request: {request.model_dump_json()}"
            ),
            text_format=EmailLanguageResult,
            store=False,
        )
        if response.output_parsed is None:
            raise RuntimeError("The language model did not return an email draft")
        return response.output_parsed

    def revise_email(self, proposal: Any, feedback: str) -> EmailLanguageResult:
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "Revise the supplied email exactly as requested. Preserve its recipient and "
                "factual content. Do not add new facts, commitments, dates, or reasons."
            ),
            input=f"Email: {proposal.model_dump_json()}\nFeedback: {feedback}",
            text_format=EmailLanguageResult,
            store=False,
        )
        if response.output_parsed is None:
            raise RuntimeError("The language model did not return a revised email")
        return response.output_parsed
