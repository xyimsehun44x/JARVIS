from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from jarvis.agents.calendar.schemas import (
    CalendarEvent,
    CalendarExecutionResult,
    CalendarProposal,
)
from jarvis.core.execution import RecoveryDecision


class CalendarProvider(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...
    def create_event(self, proposal: CalendarProposal) -> CalendarExecutionResult: ...
    def update_event(self, proposal: CalendarProposal) -> CalendarExecutionResult: ...
    def cancel_event(self, proposal: CalendarProposal) -> CalendarExecutionResult: ...
    def reconcile(
        self, proposal: CalendarProposal, external_resource_id: str | None
    ) -> RecoveryDecision: ...


class MockCalendarProvider:
    def __init__(
        self, events: list[CalendarEvent] | None = None, *, today: date | None = None
    ) -> None:
        base = today or date.today()
        tomorrow = base + timedelta(days=1)
        self.events = events if events is not None else [
            CalendarEvent(
                event_id="event-standup",
                title="Team stand-up",
                start=datetime.combine(tomorrow, time(10)),
                end=datetime.combine(tomorrow, time(10, 30)),
                attendees=["team@example.com"],
            ),
            CalendarEvent(
                event_id="event-david",
                title="Meeting with David Kim",
                start=datetime.combine(tomorrow, time(15)),
                end=datetime.combine(tomorrow, time(16)),
                attendees=["david.kim@example.com"],
            ),
            CalendarEvent(
                event_id="event-dinner",
                title="Dinner with Minho",
                start=datetime.combine(tomorrow, time(19)),
                end=datetime.combine(tomorrow, time(20, 30)),
                attendees=["minho@example.com"],
            ),
        ]

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return sorted(
            [event.model_copy(deep=True) for event in self.events if event.start < end and event.end > start],
            key=lambda event: event.start,
        )

    def create_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        event_id = (
            proposal.event_id
            or proposal.provider_operation_id
            or f"event-{uuid4().hex[:10]}"
        )
        self.events.append(
            CalendarEvent(event_id=event_id, **proposal.model_dump(exclude={"operation", "event_id"}))
        )
        return CalendarExecutionResult(ok=True, event_id=event_id)

    def update_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        for index, event in enumerate(self.events):
            if event.event_id == proposal.event_id:
                self.events[index] = CalendarEvent(
                    event_id=event.event_id,
                    title=proposal.title,
                    start=proposal.start,
                    end=proposal.end,
                    attendees=proposal.attendees,
                )
                return CalendarExecutionResult(ok=True, event_id=event.event_id)
        return CalendarExecutionResult(ok=False, error="Event not found")

    def cancel_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        for event in self.events:
            if event.event_id == proposal.event_id:
                self.events.remove(event)
                return CalendarExecutionResult(ok=True, event_id=event.event_id)
        return CalendarExecutionResult(ok=False, error="Event not found")

    def reconcile(
        self, proposal: CalendarProposal, external_resource_id: str | None
    ) -> RecoveryDecision:
        event_id = (
            external_resource_id
            or proposal.event_id
            or proposal.provider_operation_id
        )
        event = next((item for item in self.events if item.event_id == event_id), None)
        if proposal.operation == "cancel_event":
            if event is None:
                return RecoveryDecision(
                    outcome="verified",
                    external_resource_id=event_id,
                    result={
                        "ok": True,
                        "event_id": event_id,
                        "reconciled": True,
                    },
                )
            return RecoveryDecision(
                outcome="not_applied",
                external_resource_id=event_id,
                result={"ok": False, "not_applied": True, "reconciled": True},
            )
        if event is None:
            outcome = (
                "not_applied"
                if proposal.operation == "create_event"
                else "uncertain"
            )
            return RecoveryDecision(
                outcome=outcome,
                external_resource_id=event_id,
                result={
                    "ok": False,
                    "not_applied": outcome == "not_applied",
                    "outcome_uncertain": outcome == "uncertain",
                    "reconciled": True,
                },
            )
        if self._matches(event, proposal):
            return RecoveryDecision(
                outcome="verified",
                external_resource_id=event.event_id,
                result={
                    "ok": True,
                    "event_id": event.event_id,
                    "reconciled": True,
                },
            )
        return RecoveryDecision(
            outcome="uncertain",
            external_resource_id=event.event_id,
            result={
                "ok": False,
                "outcome_uncertain": True,
                "error": "Calendar event did not match the approved payload",
            },
        )

    @staticmethod
    def _matches(event: CalendarEvent, proposal: CalendarProposal) -> bool:
        return (
            event.title == proposal.title
            and event.start == proposal.start
            and event.end == proposal.end
            and sorted(event.attendees) == sorted(proposal.attendees)
        )


class GoogleCalendarProvider:
    def __init__(
        self,
        oauth,
        *,
        timezone_name: str,
        calendar_id: str = "primary",
        allow_writes: bool = False,
    ) -> None:
        self.oauth = oauth
        self.timezone_name = timezone_name
        self.tz = ZoneInfo(timezone_name)
        self.calendar_id = calendar_id
        self.allow_writes = allow_writes

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        service = self.oauth.service("calendar", "v3")
        response = (
            service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=self._aware(start).isoformat(),
                timeMax=self._aware(end).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            )
            .execute()
        )
        return [
            event
            for item in response.get("items", [])
            if (event := self._to_event(item)) is not None
        ]

    def create_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        if not self.allow_writes:
            return self._write_disabled()
        try:
            service = self.oauth.service("calendar", "v3")
        except Exception as exc:
            return CalendarExecutionResult(ok=False, error=str(exc))
        try:
            result = (
                service.events()
                .insert(
                    calendarId=self.calendar_id,
                    body=self._body(proposal, include_operation_id=True),
                    sendUpdates="all",
                )
                .execute()
            )
            return self._verify(result.get("id"), proposal)
        except Exception as exc:
            return CalendarExecutionResult(
                ok=False,
                outcome_uncertain=True,
                error=f"Calendar event creation ended without a verified outcome: {exc}",
            )

    def update_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        if not self.allow_writes:
            return self._write_disabled()
        if not proposal.event_id:
            return CalendarExecutionResult(ok=False, error="Missing event ID")
        try:
            service = self.oauth.service("calendar", "v3")
        except Exception as exc:
            return CalendarExecutionResult(ok=False, event_id=proposal.event_id, error=str(exc))
        try:
            result = (
                service.events()
                .patch(
                    calendarId=self.calendar_id,
                    eventId=proposal.event_id,
                    body=self._body(proposal),
                    sendUpdates="all",
                )
                .execute()
            )
            return self._verify(result.get("id"), proposal)
        except Exception as exc:
            return CalendarExecutionResult(
                ok=False,
                event_id=proposal.event_id,
                outcome_uncertain=True,
                error=f"Calendar event update ended without a verified outcome: {exc}",
            )

    def cancel_event(self, proposal: CalendarProposal) -> CalendarExecutionResult:
        if not self.allow_writes:
            return self._write_disabled()
        if not proposal.event_id:
            return CalendarExecutionResult(ok=False, error="Missing event ID")
        try:
            service = self.oauth.service("calendar", "v3")
        except Exception as exc:
            return CalendarExecutionResult(ok=False, event_id=proposal.event_id, error=str(exc))
        try:
            service.events().delete(
                calendarId=self.calendar_id,
                eventId=proposal.event_id,
                sendUpdates="all",
            ).execute()
            try:
                service.events().get(
                    calendarId=self.calendar_id, eventId=proposal.event_id
                ).execute()
                return CalendarExecutionResult(
                    ok=False,
                    event_id=proposal.event_id,
                    outcome_uncertain=True,
                    error="Calendar event still exists after the cancellation request",
                )
            except Exception as verification_error:
                status = getattr(getattr(verification_error, "resp", None), "status", None)
                if status not in {404, 410}:
                    raise
            return CalendarExecutionResult(ok=True, event_id=proposal.event_id)
        except Exception as exc:
            return CalendarExecutionResult(
                ok=False,
                event_id=proposal.event_id,
                outcome_uncertain=True,
                error=f"Calendar cancellation ended without a verified outcome: {exc}",
            )

    def reconcile(
        self, proposal: CalendarProposal, external_resource_id: str | None
    ) -> RecoveryDecision:
        event_id = (
            external_resource_id
            or proposal.event_id
            or proposal.provider_operation_id
        )
        if not event_id:
            return self._uncertain("The execution has no stable Calendar event ID")
        try:
            item = (
                self.oauth.service("calendar", "v3")
                .events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
        except Exception as exc:
            if not self._is_not_found(exc):
                return self._uncertain(f"Calendar reconciliation failed: {exc}")
            if proposal.operation == "cancel_event":
                return RecoveryDecision(
                    outcome="verified",
                    external_resource_id=event_id,
                    result={
                        "ok": True,
                        "event_id": event_id,
                        "reconciled": True,
                    },
                )
            if proposal.operation == "create_event":
                return RecoveryDecision(
                    outcome="not_applied",
                    external_resource_id=event_id,
                    result={
                        "ok": False,
                        "event_id": event_id,
                        "not_applied": True,
                        "reconciled": True,
                    },
                )
            return self._uncertain(
                "Calendar could not find the event targeted by the update"
            )

        if proposal.operation == "cancel_event":
            return RecoveryDecision(
                outcome="not_applied",
                external_resource_id=event_id,
                result={
                    "ok": False,
                    "event_id": event_id,
                    "not_applied": True,
                    "reconciled": True,
                },
            )
        event = self._to_event(item)
        if event is not None and MockCalendarProvider._matches(event, proposal):
            return RecoveryDecision(
                outcome="verified",
                external_resource_id=event_id,
                result={
                    "ok": True,
                    "event_id": event_id,
                    "reconciled": True,
                },
            )
        return self._uncertain(
            "The Calendar event found for this operation did not match the approved payload",
            event_id=event_id,
        )

    def _verify(
        self, event_id: str | None, proposal: CalendarProposal
    ) -> CalendarExecutionResult:
        if not event_id:
            return CalendarExecutionResult(
                ok=False,
                outcome_uncertain=True,
                error="Calendar accepted the write request but returned no event ID",
            )
        try:
            item = (
                self.oauth.service("calendar", "v3")
                .events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
        except Exception as exc:
            return CalendarExecutionResult(
                ok=False,
                event_id=event_id,
                outcome_uncertain=True,
                error=f"Calendar accepted the write but verification failed: {exc}",
            )
        event = self._to_event(item)
        ok = bool(event and MockCalendarProvider._matches(event, proposal))
        return CalendarExecutionResult(
            ok=ok,
            event_id=event_id,
            outcome_uncertain=not ok,
            error=None if ok else "Calendar verification did not match the approved payload",
        )

    def _body(
        self, proposal: CalendarProposal, *, include_operation_id: bool = False
    ) -> dict:
        body = {
            "summary": proposal.title,
            "start": {
                "dateTime": self._aware(proposal.start).isoformat(),
                "timeZone": self.timezone_name,
            },
            "end": {
                "dateTime": self._aware(proposal.end).isoformat(),
                "timeZone": self.timezone_name,
            },
            "attendees": [{"email": email} for email in proposal.attendees],
        }
        if include_operation_id and proposal.provider_operation_id:
            body["id"] = proposal.provider_operation_id
        return body

    def _to_event(self, item: dict) -> CalendarEvent | None:
        start_data = item.get("start", {})
        end_data = item.get("end", {})
        try:
            start = self._parse_datetime(start_data)
            end = self._parse_datetime(end_data)
        except (KeyError, ValueError):
            return None
        return CalendarEvent(
            event_id=item["id"],
            title=item.get("summary", "Untitled event"),
            start=start,
            end=end,
            attendees=[
                attendee["email"]
                for attendee in item.get("attendees", [])
                if attendee.get("email")
            ],
        )

    def _parse_datetime(self, value: dict) -> datetime:
        if "dateTime" in value:
            parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
            return parsed.astimezone(self.tz).replace(tzinfo=None)
        return datetime.combine(date.fromisoformat(value["date"]), time.min)

    def _aware(self, value: datetime) -> datetime:
        return value.replace(tzinfo=self.tz) if value.tzinfo is None else value.astimezone(self.tz)

    @staticmethod
    def _write_disabled() -> CalendarExecutionResult:
        return CalendarExecutionResult(
            ok=False,
            error="Live Calendar writes are disabled by JARVIS_ALLOW_CALENDAR_WRITES",
        )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return getattr(getattr(exc, "resp", None), "status", None) in {404, 410}

    @staticmethod
    def _uncertain(error: str, *, event_id: str | None = None) -> RecoveryDecision:
        return RecoveryDecision(
            outcome="uncertain",
            external_resource_id=event_id,
            result={"ok": False, "outcome_uncertain": True, "error": error},
        )
