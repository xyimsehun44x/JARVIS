from __future__ import annotations

import re

from jarvis.agents.email.schemas import EmailProposal, EmailRequest, ResolvedContact
from jarvis.integrations.contacts import ContactProvider


class EmailAgent:
    def __init__(self, contacts: ContactProvider, language_model=None) -> None:
        self.contacts = contacts
        self.language_model = language_model

    def resolve_contact(self, query: str) -> list[ResolvedContact]:
        return self.contacts.search(query)

    def request_from_text(self, text: str) -> EmailRequest:
        lowered = text.lower()
        operation = "draft" if "draft" in lowered and "send" not in lowered else "send"
        recipient = self._recipient_from_text(text)
        reason = None
        reason_match = re.search(r"(?:because|for)\s+(.+?)(?:[.!]|$)", text, re.I)
        if reason_match:
            reason = reason_match.group(1).strip()
        desired_outcome = self._desired_outcome(text)
        if reason:
            desired_outcome = re.sub(
                rf"\s+for\s+{re.escape(reason)}$", "", desired_outcome, flags=re.I
            ).strip()
        return EmailRequest(
            operation=operation,
            recipient_name=recipient,
            topic=text.strip(),
            desired_outcome=desired_outcome,
            reason=reason,
        )

    def compose(self, request: EmailRequest, contact: ResolvedContact) -> EmailProposal:
        if self.language_model and hasattr(self.language_model, "draft_email"):
            draft = self.language_model.draft_email(request, contact)
            return EmailProposal(
                recipient_name=contact.name,
                recipient_email=contact.email,
                subject=draft.subject,
                body=draft.body,
                operation="send" if request.operation == "send" else "save_draft",
            )
        outcome = request.desired_outcome or "get in touch"
        subject = self._subject(outcome)
        greeting = f"Hi {contact.name.split()[0]},"
        if request.tone in {"friendly", "casual"}:
            body = f"{greeting}\n\nJust a quick note to {outcome}."
        else:
            body = f"{greeting}\n\nI’m writing to {outcome}."
        if request.reason:
            body += f" The reason is {request.reason}."
        body += "\n\nBest,"
        return EmailProposal(
            recipient_name=contact.name,
            recipient_email=contact.email,
            subject=subject,
            body=body,
            operation="send" if request.operation == "send" else "save_draft",
        )

    def compose_availability(
        self, contact: ResolvedContact, slots: list[str], *, operation: str = "send"
    ) -> EmailProposal:
        options = "\n".join(f"- {slot}" for slot in slots)
        return EmailProposal(
            recipient_name=contact.name,
            recipient_email=contact.email,
            subject="Meeting availability",
            body=(
                f"Hi {contact.name.split()[0]},\n\nWould any of these times work for you?\n"
                f"{options}\n\nBest,"
            ),
            operation="send" if operation == "send" else "save_draft",
        )

    def revise(self, proposal: EmailProposal, feedback: str) -> EmailProposal:
        if self.language_model and hasattr(self.language_model, "revise_email"):
            draft = self.language_model.revise_email(proposal, feedback)
            return proposal.model_copy(update={"subject": draft.subject, "body": draft.body})
        updated = proposal.model_copy(deep=True)
        lowered = feedback.lower()
        if (
            "less formal" in lowered
            or "less stiff" in lowered
            or "friendlier" in lowered
            or "friendly" in lowered
        ):
            updated.body = updated.body.replace("I’m writing to", "Just a quick note to")
            updated.body = updated.body.replace("Best,", "Thanks,")
        if "more formal" in lowered:
            updated.body = updated.body.replace("Just a quick note to", "I’m writing to")
            updated.body = updated.body.replace("Thanks,", "Best regards,")
        if "don't mention family" in lowered or "do not mention family" in lowered:
            updated.body = re.sub(
                r"\s*(?:The reason is|This is)[^.]*family[^.]*\.", "", updated.body, flags=re.I
            )
            updated.body = re.sub(r"\s+for family[^.\n]*", "", updated.body, flags=re.I)
        subject_match = re.search(r"subject\s+(?:to|is)\s+[\"']?(.+?)[\"']?$", feedback, re.I)
        if subject_match:
            updated.subject = subject_match.group(1).strip()
        return updated

    @staticmethod
    def _recipient_from_text(text: str) -> str | None:
        email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        if email:
            return email.group(0)
        match = re.search(
            r"(?:email|e-mail|mail)\s+(?:to\s+)?(.+?)"
            r"(?=\s+(?:and|about|saying|say|telling|to tell|asking|to ask)\b"
            r"|\s+(?:a\s+(?:couple\s+of|quick)|some|two)\s+(?:options|updates?|notes?)\b"
            r"|[,.]|$)",
            text,
            re.I,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _desired_outcome(text: str) -> str:
        match = re.search(r"(?:tell|saying|say|ask)\s+(?:him|her|them|\w+)\s+(.+)", text, re.I)
        if match:
            return match.group(1).rstrip(". ")
        if "apolog" in text.lower() or "sorry" in text.lower():
            return "apologize for moving our meeting"
        return "follow up with you"

    @staticmethod
    def _subject(outcome: str) -> str:
        lowered = outcome.lower()
        if "meeting" in lowered or "reschedul" in lowered or "move" in lowered:
            return "Meeting update"
        if "apolog" in lowered or "sorry" in lowered:
            return "A quick apology"
        return "Quick note"
