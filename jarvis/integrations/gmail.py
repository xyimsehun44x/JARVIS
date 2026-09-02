from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Protocol
from uuid import uuid4

from jarvis.agents.email.schemas import EmailExecutionResult, EmailProposal


class GmailProvider(Protocol):
    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult: ...
    def send(self, proposal: EmailProposal) -> EmailExecutionResult: ...


class MockGmailProvider:
    def __init__(self) -> None:
        self.drafts: list[EmailProposal] = []
        self.sent: list[EmailProposal] = []

    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult:
        self.drafts.append(proposal.model_copy(deep=True))
        return EmailExecutionResult(
            ok=True, operation="save_draft", draft_id=f"draft-{uuid4().hex[:10]}"
        )

    def send(self, proposal: EmailProposal) -> EmailExecutionResult:
        self.sent.append(proposal.model_copy(deep=True))
        return EmailExecutionResult(
            ok=True, operation="send", message_id=f"message-{uuid4().hex[:10]}"
        )


class GoogleGmailProvider:
    def __init__(self, oauth, *, allow_send: bool = False) -> None:
        self.oauth = oauth
        self.allow_send = allow_send

    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult:
        try:
            service = self.oauth.service("gmail", "v1")
            result = (
                service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": self._raw(proposal)}})
                .execute()
            )
            draft_id = result.get("id")
            if not draft_id:
                return EmailExecutionResult(
                    ok=False, operation="save_draft", error="Gmail returned no draft ID"
                )
            verified = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="minimal")
                .execute()
            )
            return EmailExecutionResult(
                ok=bool(verified.get("id")), operation="save_draft", draft_id=draft_id
            )
        except Exception as exc:
            return EmailExecutionResult(ok=False, operation="save_draft", error=str(exc))

    def send(self, proposal: EmailProposal) -> EmailExecutionResult:
        if not self.allow_send:
            return EmailExecutionResult(
                ok=False,
                operation="send",
                error="Live Gmail sending is disabled by JARVIS_ALLOW_EMAIL_SEND",
            )
        try:
            service = self.oauth.service("gmail", "v1")
            result = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": self._raw(proposal)})
                .execute()
            )
            message_id = result.get("id")
            if not message_id:
                return EmailExecutionResult(
                    ok=False, operation="send", error="Gmail returned no message ID"
                )
            verified = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="minimal")
                .execute()
            )
            return EmailExecutionResult(
                ok=bool(verified.get("id")), operation="send", message_id=message_id
            )
        except Exception as exc:
            return EmailExecutionResult(ok=False, operation="send", error=str(exc))

    @staticmethod
    def _raw(proposal: EmailProposal) -> str:
        message = EmailMessage()
        message["To"] = proposal.recipient_email
        message["Subject"] = proposal.subject
        message.set_content(proposal.body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
