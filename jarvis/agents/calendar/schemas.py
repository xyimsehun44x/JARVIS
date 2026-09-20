from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CalendarRequest(BaseModel):
    operation: Literal[
        "list_events",
        "find_free_time",
        "create_event",
        "update_event",
        "cancel_event",
    ]
    event_reference: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    attendees: list[str] = Field(default_factory=list)
    notes: str | None = None


class CalendarEvent(BaseModel):
    event_id: str
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list)


class CalendarProposal(BaseModel):
    operation: Literal["create_event", "update_event", "cancel_event"]
    event_id: str | None = None
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = Field(default_factory=list)


class CalendarExecutionResult(BaseModel):
    ok: bool
    event_id: str | None = None
    outcome_uncertain: bool = False
    error: str | None = None
