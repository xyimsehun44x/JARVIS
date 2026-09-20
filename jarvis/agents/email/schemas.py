from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EmailRequest(BaseModel):
    operation: Literal["prepare", "save_draft", "send"] = "prepare"
    recipient_name: str | None = None
    recipient_email: str | None = None
    topic: str | None = None
    desired_outcome: str | None = None
    reason: str | None = None
    tone: str = "professional"


class ResolvedContact(BaseModel):
    name: str
    email: str


class EmailProposal(BaseModel):
    recipient_name: str
    recipient_email: str
    subject: str
    body: str
    operation: Literal["prepare", "save_draft", "send"]


class EmailExecutionResult(BaseModel):
    ok: bool
    operation: Literal["save_draft", "send"]
    message_id: str | None = None
    draft_id: str | None = None
    outcome_uncertain: bool = False
    error: str | None = None
