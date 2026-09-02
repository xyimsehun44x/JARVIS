from __future__ import annotations

from datetime import date, datetime, time, timedelta

from jarvis.agents.calendar.schemas import CalendarEvent, CalendarProposal
from jarvis.integrations.calendar import CalendarProvider


class CalendarAgent:
    def __init__(self, provider: CalendarProvider) -> None:
        self.provider = provider

    def events_for_day(self, day: date) -> list[CalendarEvent]:
        start = datetime.combine(day, time.min)
        return self.provider.list_events(start, start + timedelta(days=1))

    def find_events(
        self, reference: str, candidates: list[CalendarEvent] | None = None
    ) -> list[CalendarEvent]:
        pool = candidates if candidates is not None else self.provider.list_events(
            datetime.now() - timedelta(days=1), datetime.now() + timedelta(days=30)
        )
        stop_words = {"the", "with", "that", "this", "tomorrow", "today", "meeting"}
        words = [
            word.strip("'s,.-")
            for word in reference.lower().split()
            if len(word) > 2 and word.strip("'s,.-") not in stop_words
        ]
        if not words:
            meeting_matches = [event for event in pool if "meeting" in event.title.lower()]
            return meeting_matches or pool
        matches = []
        for event in pool:
            haystack = f"{event.title} {' '.join(event.attendees)}".lower()
            if any(word in haystack for word in words):
                matches.append(event)
        return matches

    def free_slots(
        self,
        start_day: date,
        *,
        days: int = 5,
        duration: timedelta = timedelta(hours=1),
        limit: int = 3,
    ) -> list[datetime]:
        slots: list[datetime] = []
        for offset in range(days):
            day = start_day + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            events = self.events_for_day(day)
            for hour in (10, 13, 15, 17):
                start = datetime.combine(day, time(hour))
                end = start + duration
                if all(end <= event.start or start >= event.end for event in events):
                    slots.append(start)
                    if len(slots) == limit:
                        return slots
        return slots

    @staticmethod
    def update_proposal(event: CalendarEvent, start: datetime) -> CalendarProposal:
        return CalendarProposal(
            operation="update_event",
            event_id=event.event_id,
            title=event.title,
            start=start,
            end=start + (event.end - event.start),
            attendees=event.attendees,
        )

    @staticmethod
    def cancel_proposal(event: CalendarEvent) -> CalendarProposal:
        return CalendarProposal(
            operation="cancel_event",
            event_id=event.event_id,
            title=event.title,
            start=event.start,
            end=event.end,
            attendees=event.attendees,
        )
