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


class GeminiConversationModel:
    """Production language boundary using the native Gemini Interactions API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        thinking_level: str = "low",
        client: Any | None = None,
    ) -> None:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model
        self.thinking_level = thinking_level

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def respond(self, text: str, history: list[dict[str, str]]) -> str:
        interaction = self.client.interactions.create(
            model=self.model,
            system_instruction=JARVIS_PERSONA,
            input=self._conversation_input(text, history),
            generation_config={
                "thinking_level": self.thinking_level,
                "max_output_tokens": 160,
            },
            store=False,
        )
        output = interaction.output_text.strip()
        if not output:
            raise RuntimeError("Gemini returned an empty response")
        return output

    def classify(self, text: str, history: list[dict[str, str]]) -> str:
        parsed = self._structured(
            TurnClassification,
            system_instruction=(
                "Classify the latest turn for a personal assistant. Use conversation for "
                "ordinary dialogue, email for email-only work, calendar for calendar-only "
                "work, and cross_domain only when both calendar data and email are required."
            ),
            input_text=self._conversation_input(text, history[-8:]),
            thinking_level="low",
        )
        return parsed.route

    def draft_email(self, request: Any, contact: Any) -> EmailLanguageResult:
        return self._structured(
            EmailLanguageResult,
            system_instruction=(
                "Draft a concise email. Do not invent facts, dates, reasons, promises, or a "
                "sender signature. Use only the supplied request and resolved contact."
            ),
            input_text=(
                f"Resolved recipient: {contact.model_dump_json()}\n"
                f"Request: {request.model_dump_json()}"
            ),
            thinking_level="low",
        )

    def revise_email(self, proposal: Any, feedback: str) -> EmailLanguageResult:
        return self._structured(
            EmailLanguageResult,
            system_instruction=(
                "Revise the supplied email exactly as requested. Preserve its recipient and "
                "factual content. Do not add new facts, commitments, dates, or reasons."
            ),
            input_text=f"Email: {proposal.model_dump_json()}\nFeedback: {feedback}",
            thinking_level="low",
        )

    def _structured(
        self,
        schema: type[BaseModel],
        *,
        system_instruction: str,
        input_text: str,
        thinking_level: str,
    ) -> BaseModel:
        interaction = self.client.interactions.create(
            model=self.model,
            system_instruction=system_instruction,
            input=input_text,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema.model_json_schema(),
            },
            generation_config={"thinking_level": thinking_level},
            store=False,
        )
        if not interaction.output_text:
            raise RuntimeError("Gemini did not return structured output")
        return schema.model_validate_json(interaction.output_text)

    @staticmethod
    def _conversation_input(text: str, history: list[dict[str, str]]) -> str:
        turns = [
            f"{message.get('role', 'user').title()}: {message.get('content', '')}"
            for message in history[-20:]
        ]
        turns.append(f"User: {text}")
        return "Conversation so far:\n" + "\n".join(turns)
