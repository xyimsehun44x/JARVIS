from __future__ import annotations

import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Protocol
from uuid import uuid4

from jarvis.agents.email.schemas import EmailExecutionResult, EmailProposal
from jarvis.core.execution import RecoveryDecision


class GmailProvider(Protocol):
    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult: ...
    def send(self, proposal: EmailProposal) -> EmailExecutionResult: ...
    def reconcile(
        self, proposal: EmailProposal, external_resource_id: str | None
    ) -> RecoveryDecision: ...


class MockGmailProvider:
    def __init__(self) -> None:
        self.drafts: list[EmailProposal] = []
        self.sent: list[EmailProposal] = []
        self._operations: dict[str, EmailExecutionResult] = {}

    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult:
        self.drafts.append(proposal.model_copy(deep=True))
        result = EmailExecutionResult(
            ok=True, operation="save_draft", draft_id=f"draft-{uuid4().hex[:10]}"
        )
        if proposal.provider_operation_id:
            self._operations[proposal.provider_operation_id] = result
        return result

    def send(self, proposal: EmailProposal) -> EmailExecutionResult:
        self.sent.append(proposal.model_copy(deep=True))
        result = EmailExecutionResult(
            ok=True, operation="send", message_id=f"message-{uuid4().hex[:10]}"
        )
        if proposal.provider_operation_id:
            self._operations[proposal.provider_operation_id] = result
        return result

    def reconcile(
        self, proposal: EmailProposal, external_resource_id: str | None
    ) -> RecoveryDecision:
        result = self._operations.get(proposal.provider_operation_id or "")
        if result is None:
            return RecoveryDecision(
                outcome="not_applied",
                result={"ok": False, "reconciled": True, "not_applied": True},
            )
        resource_id = result.message_id or result.draft_id
        return RecoveryDecision(
            outcome="verified",
            external_resource_id=resource_id,
            result={**result.model_dump(mode="json"), "reconciled": True},
        )


class GoogleGmailProvider:
    def __init__(self, oauth, *, allow_send: bool = False) -> None:
        self.oauth = oauth
        self.allow_send = allow_send

    def save_draft(self, proposal: EmailProposal) -> EmailExecutionResult:
        try:
            service = self.oauth.service("gmail", "v1")
        except Exception as exc:
            return EmailExecutionResult(ok=False, operation="save_draft", error=str(exc))
        try:
            result = (
                service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": self._raw(proposal)}})
                .execute()
            )
            draft_id = result.get("id")
            if not draft_id:
                return EmailExecutionResult(
                    ok=False,
                    operation="save_draft",
                    outcome_uncertain=True,
                    error="Gmail accepted the draft request but returned no draft ID",
                )
        except Exception as exc:
            return EmailExecutionResult(
                ok=False,
                operation="save_draft",
                outcome_uncertain=True,
                error=f"Gmail draft creation ended without a verified outcome: {exc}",
            )
        try:
            verified = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="minimal")
                .execute()
            )
            return EmailExecutionResult(
                ok=verified.get("id") == draft_id,
                operation="save_draft",
                draft_id=draft_id,
                outcome_uncertain=verified.get("id") != draft_id,
                error=(
                    None
                    if verified.get("id") == draft_id
                    else "Gmail draft verification did not match the created draft"
                ),
            )
        except Exception as exc:
            return EmailExecutionResult(
                ok=False,
                operation="save_draft",
                draft_id=draft_id,
                outcome_uncertain=True,
                error=f"Gmail created the draft but verification failed: {exc}",
            )

    def send(self, proposal: EmailProposal) -> EmailExecutionResult:
        if not self.allow_send:
            return EmailExecutionResult(
                ok=False,
                operation="send",
                error="Live Gmail sending is disabled by JARVIS_ALLOW_EMAIL_SEND",
            )
        try:
            service = self.oauth.service("gmail", "v1")
        except Exception as exc:
            return EmailExecutionResult(ok=False, operation="send", error=str(exc))
        try:
            result = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": self._raw(proposal)})
                .execute()
            )
            message_id = result.get("id")
            if not message_id:
                return EmailExecutionResult(
                    ok=False,
                    operation="send",
                    outcome_uncertain=True,
                    error="Gmail accepted the send request but returned no message ID",
                )
        except Exception as exc:
            return EmailExecutionResult(
                ok=False,
                operation="send",
                outcome_uncertain=True,
                error=f"Gmail send ended without a verified outcome: {exc}",
            )
        try:
            verified = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="minimal")
                .execute()
            )
            return EmailExecutionResult(
                ok=verified.get("id") == message_id,
                operation="send",
                message_id=message_id,
                outcome_uncertain=verified.get("id") != message_id,
                error=(
                    None
                    if verified.get("id") == message_id
                    else "Gmail verification did not match the sent message"
                ),
            )
        except Exception as exc:
            return EmailExecutionResult(
                ok=False,
                operation="send",
                message_id=message_id,
                outcome_uncertain=True,
                error=f"Gmail accepted the message but verification failed: {exc}",
            )

    def reconcile(
        self, proposal: EmailProposal, external_resource_id: str | None
    ) -> RecoveryDecision:
        operation_id = proposal.provider_operation_id
        if not operation_id:
            return self._uncertain("The execution has no stable Gmail operation ID")
        try:
            service = self.oauth.service("gmail", "v1")
            if proposal.operation == "save_draft":
                return self._reconcile_draft(
                    service, proposal, operation_id, external_resource_id
                )
            return self._reconcile_message(
                service, proposal, operation_id, external_resource_id
            )
        except Exception as exc:
            return self._uncertain(f"Gmail reconciliation failed: {exc}")

    def _reconcile_message(
        self,
        service,
        proposal: EmailProposal,
        operation_id: str,
        external_resource_id: str | None,
    ) -> RecoveryDecision:
        message = None
        if external_resource_id:
            try:
                message = (
                    service.users()
                    .messages()
                    .get(userId="me", id=external_resource_id, format="raw")
                    .execute()
                )
            except Exception as exc:
                if not self._is_not_found(exc):
                    return self._uncertain(f"Gmail message verification failed: {exc}")
        if message is None:
            matches = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=f"in:sent rfc822msgid:<{operation_id}@jarvis.local>",
                    maxResults=2,
                )
                .execute()
                .get("messages", [])
            )
            if len(matches) != 1:
                return self._uncertain(
                    "Gmail could not prove whether the message was sent; no retry was made"
                )
            external_resource_id = matches[0].get("id")
            message = (
                service.users()
                .messages()
                .get(userId="me", id=external_resource_id, format="raw")
                .execute()
            )
        if not self._matches(message, proposal, operation_id):
            return self._uncertain(
                "The Gmail message found for this operation did not match the approved payload"
            )
        return RecoveryDecision(
            outcome="verified",
            external_resource_id=external_resource_id,
            result={
                "ok": True,
                "operation": "send",
                "message_id": external_resource_id,
                "reconciled": True,
            },
        )

    def _reconcile_draft(
        self,
        service,
        proposal: EmailProposal,
        operation_id: str,
        external_resource_id: str | None,
    ) -> RecoveryDecision:
        draft = None
        if external_resource_id:
            try:
                draft = (
                    service.users()
                    .drafts()
                    .get(userId="me", id=external_resource_id, format="raw")
                    .execute()
                )
            except Exception as exc:
                if not self._is_not_found(exc):
                    return self._uncertain(f"Gmail draft verification failed: {exc}")
        if draft is None:
            matches = (
                service.users()
                .drafts()
                .list(
                    userId="me",
                    q=f"rfc822msgid:<{operation_id}@jarvis.local>",
                    maxResults=2,
                )
                .execute()
                .get("drafts", [])
            )
            if len(matches) != 1:
                return self._uncertain(
                    "Gmail could not prove whether the draft was created; no retry was made"
                )
            draft_id = matches[0].get("id")
            if not draft_id:
                return self._uncertain(
                    "Gmail found the draft but did not return its immutable draft ID"
                )
            external_resource_id = draft_id
            draft = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="raw")
                .execute()
            )
        if not self._matches(draft.get("message", {}), proposal, operation_id):
            return self._uncertain(
                "The Gmail draft found for this operation did not match the approved payload"
            )
        return RecoveryDecision(
            outcome="verified",
            external_resource_id=external_resource_id,
            result={
                "ok": True,
                "operation": "save_draft",
                "draft_id": external_resource_id,
                "reconciled": True,
            },
        )

    @classmethod
    def _matches(
        cls, resource: dict, proposal: EmailProposal, operation_id: str
    ) -> bool:
        raw = resource.get("raw")
        if not raw:
            return False
        try:
            padding = "=" * (-len(raw) % 4)
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(raw + padding)
            )
            body_part = message.get_body(preferencelist=("plain",))
            body = (body_part or message).get_content().strip()
        except Exception:
            return False
        return (
            message.get("Message-ID") == f"<{operation_id}@jarvis.local>"
            and message.get("To") == proposal.recipient_email
            and message.get("Subject") == proposal.subject
            and body == proposal.body.strip()
        )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return getattr(getattr(exc, "resp", None), "status", None) in {404, 410}

    @staticmethod
    def _uncertain(error: str) -> RecoveryDecision:
        return RecoveryDecision(
            outcome="uncertain",
            result={"ok": False, "outcome_uncertain": True, "error": error},
        )

    @staticmethod
    def _raw(proposal: EmailProposal) -> str:
        message = EmailMessage()
        message["To"] = proposal.recipient_email
        message["Subject"] = proposal.subject
        if proposal.provider_operation_id:
            message["Message-ID"] = (
                f"<{proposal.provider_operation_id}@jarvis.local>"
            )
        message.set_content(proposal.body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
