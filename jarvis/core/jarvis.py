from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from langgraph.types import Command, interrupt

from jarvis.agents.calendar.agent import CalendarAgent
from jarvis.agents.calendar.schemas import CalendarEvent, CalendarProposal
from jarvis.agents.email.agent import EmailAgent
from jarvis.agents.email.schemas import EmailProposal, EmailRequest, ResolvedContact
from jarvis.config import Settings
from jarvis.core.brain import (
    ConversationModel,
    GeminiConversationModel,
    OpenAIConversationModel,
    RuleBasedConversationModel,
)
from jarvis.core.execution import ExecutionRecord, ExecutionRegistry
from jarvis.core.graph import build_graph
from jarvis.core.permissions import PermissionPolicy, ProposedAction, RiskLevel
from jarvis.core.router import Route, Router
from jarvis.core.state import JarvisState
from jarvis.integrations.calendar import CalendarProvider, MockCalendarProvider
from jarvis.integrations.contacts import ContactProvider, StaticContactProvider
from jarvis.integrations.gmail import GmailProvider, MockGmailProvider
from jarvis.memory.long_term import InMemoryLongTermMemory, SQLiteLongTermMemory


APPROVE_WORDS = ("yes", "approve", "send it", "do it", "go ahead", "confirm", "perfect")
REJECT_WORDS = ("no", "reject", "cancel", "never mind", "nevermind", "forget it", "stop")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(slots=True)
class TurnResult:
    response: str | None = None
    needs_input: bool = False
    prompt: str | None = None
    interrupt_kind: str | None = None
    proposal: dict[str, Any] | None = None
    state: dict[str, Any] | None = None


class Jarvis:
    """One user-facing assistant backed by resumable specialist workflows."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        contacts: ContactProvider | None = None,
        gmail: GmailProvider | None = None,
        calendar: CalendarProvider | None = None,
        conversation_model: ConversationModel | None = None,
        checkpointer=None,
        execution_registry: ExecutionRegistry | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._checkpoint_connection: sqlite3.Connection | None = None
        self.google_oauth = None
        self.brain = conversation_model or self._build_language_model()

        if self.settings.mode == "google":
            if not (contacts and gmail and calendar):
                from jarvis.integrations.calendar import GoogleCalendarProvider
                from jarvis.integrations.contacts import GooglePeopleProvider
                from jarvis.integrations.gmail import GoogleGmailProvider
                from jarvis.integrations.google_auth import GoogleOAuth, scopes_for_settings

                self.google_oauth = GoogleOAuth(
                    self.settings.google_credentials_path,
                    self.settings.google_token_path,
                    scopes_for_settings(self.settings),
                )
                contacts = contacts or GooglePeopleProvider(self.google_oauth)
                gmail = gmail or GoogleGmailProvider(
                    self.google_oauth, allow_send=self.settings.allow_email_send
                )
                calendar = calendar or GoogleCalendarProvider(
                    self.google_oauth,
                    timezone_name=self.settings.timezone,
                    allow_writes=self.settings.allow_calendar_writes,
                )
        elif self.settings.mode != "mock":
            raise ValueError("JARVIS_MODE must be 'mock' or 'google'")

        self.gmail = gmail or MockGmailProvider()
        self.calendar = calendar or MockCalendarProvider()
        self.email_agent = EmailAgent(
            contacts or StaticContactProvider(), language_model=self.brain
        )
        self.calendar_agent = CalendarAgent(self.calendar)
        self.router = Router()
        self.permissions = PermissionPolicy(confirm_reversible=self.settings.confirm_drafts)
        database_path = (
            self.settings.database_path if self.settings.persistence == "sqlite" else None
        )
        self.memory = (
            SQLiteLongTermMemory(database_path)
            if database_path
            else InMemoryLongTermMemory()
        )
        self.executions = execution_registry or ExecutionRegistry(database_path)
        if checkpointer is None and database_path:
            from langgraph.checkpoint.sqlite import SqliteSaver

            self._checkpoint_connection = sqlite3.connect(
                database_path, check_same_thread=False
            )
            checkpointer = SqliteSaver(self._checkpoint_connection)
        self.app = build_graph(self._run_turn, checkpointer=checkpointer)
        self._interrupted_threads: set[str] = set()
        self._task_context: ContextVar[str | None] = ContextVar(
            "jarvis_task_id", default=None
        )

    def _build_language_model(self) -> ConversationModel:
        if self.settings.llm_provider == "rules":
            return RuleBasedConversationModel()
        if self.settings.llm_provider == "openai":
            if not self.settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when JARVIS_LLM_PROVIDER=openai")
            return OpenAIConversationModel(
                model=self.settings.openai_model,
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url,
            )
        if self.settings.llm_provider == "gemini":
            if not self.settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY is required when JARVIS_LLM_PROVIDER=gemini")
            return GeminiConversationModel(
                model=self.settings.gemini_model,
                api_key=self.settings.gemini_api_key,
                thinking_level=self.settings.gemini_thinking_level,
            )
        raise ValueError(f"Unsupported language model provider: {self.settings.llm_provider}")

    def close(self) -> None:
        self.executions.close()
        close_brain = getattr(self.brain, "close", None)
        if close_brain:
            close_brain()
        close_memory = getattr(self.memory, "close", None)
        if close_memory:
            close_memory()
        if self._checkpoint_connection:
            self._checkpoint_connection.close()
            self._checkpoint_connection = None

    def turn(self, text: str, *, thread_id: str = "default") -> TurnResult:
        if thread_id in self._interrupted_threads or self._has_pending_interrupt(thread_id):
            return self.resume(text, thread_id=thread_id)
        result = self.app.invoke(
            {"latest_user_text": text, "task_id": str(uuid4())},
            config={"configurable": {"thread_id": thread_id}},
        )
        return self._result(result, thread_id)

    def resume(self, text: str, *, thread_id: str = "default") -> TurnResult:
        result = self.app.invoke(
            Command(resume=text),
            config={"configurable": {"thread_id": thread_id}},
        )
        return self._result(result, thread_id)

    def state(self, *, thread_id: str = "default") -> dict[str, Any]:
        snapshot = self.app.get_state({"configurable": {"thread_id": thread_id}})
        return dict(snapshot.values) if snapshot else {}

    def _has_pending_interrupt(self, thread_id: str) -> bool:
        snapshot = self.app.get_state({"configurable": {"thread_id": thread_id}})
        return bool(snapshot and any(task.interrupts for task in snapshot.tasks))

    def _result(self, result: dict[str, Any], thread_id: str) -> TurnResult:
        interruptions = result.get("__interrupt__", ())
        if interruptions:
            self._interrupted_threads.add(thread_id)
            value = interruptions[0].value
            if isinstance(value, str):
                value = {"prompt": value}
            return TurnResult(
                needs_input=True,
                prompt=value.get("prompt"),
                interrupt_kind=value.get("kind"),
                proposal=value.get("action"),
                state=result,
            )
        self._interrupted_threads.discard(thread_id)
        return TurnResult(response=result.get("response_text"), state=result)

    def _run_turn(self, state: JarvisState) -> dict[str, Any]:
        text = state.get("latest_user_text", "").strip()
        history = state.get("messages", [])
        messages: list[dict[str, str]] = [{"role": "user", "content": text}]
        try:
            explicit_route = self.router.explicit_route(text)
            if explicit_route is not None:
                route = explicit_route
            elif self.router.may_need_model_routing(text):
                classifier = getattr(self.brain, "classify", None)
                route = Route(classifier(text, history)) if classifier else self.router.route(text)
            else:
                route = Route.CONVERSATION
        except Exception:
            # A model outage must not turn a potential action into ordinary banter.
            route = self.router.route(text)
        task_id = state.get("task_id") or str(uuid4())
        self._task_context.set(task_id)

        if not text:
            response = "I didn’t catch that."
            return self._complete(messages, response, task_id)

        if route is Route.CONVERSATION:
            response = self.brain.respond(text, history)
            return self._complete(messages, response, task_id)

        if route is Route.EMAIL:
            response, execution = self._email_workflow(text, messages)
            return self._complete(messages, response, task_id, "email", execution=execution)

        if route is Route.CROSS_DOMAIN:
            response, execution = self._availability_email_workflow(text, messages)
            return self._complete(messages, response, task_id, "email+calendar", execution=execution)

        response, recent_events, execution = self._calendar_workflow(text, state, messages)
        return self._complete(
            messages,
            response,
            task_id,
            "calendar",
            recent_events=recent_events,
            execution=execution,
        )

    @staticmethod
    def _complete(
        messages: list[dict[str, str]],
        response: str,
        task_id: str,
        domain: str | None = None,
        *,
        recent_events: list[dict[str, Any]] | None = None,
        execution: ExecutionRecord | None = None,
    ) -> dict[str, Any]:
        messages.append({"role": "assistant", "content": response})
        update: dict[str, Any] = {
            "messages": messages,
            "task_id": task_id,
            "task_status": "completed",
            "active_domain": None,
            "task_summary": None,
            "delegated_task": None,
            "agent_result": None,
            "proposed_action": None,
            "approval_status": None,
            "user_feedback": None,
            "response_text": response,
        }
        if recent_events is not None:
            update["recent_events"] = recent_events
        if execution is not None:
            update["execution_id"] = execution.execution_id
            update["execution_result"] = execution.result
            if execution.status != "verified":
                update["task_status"] = "failed"
        return update

    def _ask(
        self,
        messages: list[dict[str, str]],
        *,
        kind: str,
        prompt: str,
        action: ProposedAction | None = None,
    ) -> str:
        payload: dict[str, Any] = {"kind": kind, "prompt": prompt}
        if action:
            payload["action"] = action.model_dump(mode="json")
        answer = str(interrupt(payload)).strip()
        messages.extend(
            [
                {"role": "assistant", "content": prompt},
                {"role": "user", "content": answer},
            ]
        )
        return answer

    def _resolve_contact(
        self,
        query: str | None,
        messages: list[dict[str, str]],
    ) -> ResolvedContact | None:
        while not query:
            answer = self._ask(
                messages,
                kind="clarification",
                prompt="Who would you like me to email?",
            )
            if self._is_rejection(answer):
                return None
            query = self._contact_query_from_answer(answer)
        while True:
            matches = self.email_agent.resolve_contact(query)
            if len(matches) == 1:
                return matches[0]
            if not matches:
                answer = self._ask(
                    messages,
                    kind="clarification",
                    prompt=f"I couldn’t find {query}. What is their full name or email address?",
                )
                if self._is_rejection(answer):
                    return None
                query = self._contact_query_from_answer(answer)
                continue
            choices = " or ".join(f"{item.name} <{item.email}>" for item in matches)
            answer = self._ask(
                messages,
                kind="clarification",
                prompt=f"Which one did you mean: {choices}?",
            )
            if self._is_rejection(answer):
                return None
            selected = self._select_choice(answer, matches)
            if selected:
                return selected
            query = self._contact_query_from_answer(answer)

    def _email_workflow(
        self, text: str, messages: list[dict[str, str]]
    ) -> tuple[str, ExecutionRecord | None]:
        request = self.email_agent.request_from_text(text)
        contact = self._resolve_contact(request.recipient_name or request.recipient_email, messages)
        if contact is None:
            return "Very well. I haven’t sent anything.", None
        proposal = self.email_agent.compose(request, contact)
        return self._execute_email_proposal(proposal, messages)

    def _availability_email_workflow(
        self, text: str, messages: list[dict[str, str]]
    ) -> tuple[str, ExecutionRecord | None]:
        start = self._next_monday(date.today()) if "next week" in text.lower() else date.today()
        slots = self.calendar_agent.free_slots(start, limit=2)
        if not slots:
            return "I couldn’t find a suitable free slot in that range.", None
        request = self.email_agent.request_from_text(text)
        contact = self._resolve_contact(request.recipient_name or request.recipient_email, messages)
        if contact is None:
            return "Very well. I haven’t sent anything.", None
        labels = [slot.strftime("%A at %I:%M %p").replace(" 0", " ") for slot in slots]
        proposal = self.email_agent.compose_availability(contact, labels, operation=request.operation)
        return self._execute_email_proposal(proposal, messages)

    def _execute_email_proposal(
        self, proposal: EmailProposal, messages: list[dict[str, str]]
    ) -> tuple[str, ExecutionRecord | None]:
        while True:
            risk = RiskLevel.EXTERNAL_WRITE if proposal.operation == "send" else RiskLevel.REVERSIBLE
            action = ProposedAction(
                tool_name=f"gmail.{proposal.operation}",
                risk=risk,
                summary=f"{proposal.operation.replace('_', ' ').title()} email to {proposal.recipient_name}",
                payload=proposal.model_dump(mode="json"),
                idempotency_key=f"{self._task_context.get()}:{proposal.operation}",
            )
            if not self.permissions.requires_confirmation(action):
                break
            answer = self._ask(
                messages,
                kind="approval",
                prompt=self._format_email_approval(proposal),
                action=action,
            )
            if self._is_rejection(answer):
                return "Very well. I haven’t sent anything.", None
            if self._is_approval(answer):
                break
            proposal = self.email_agent.revise(proposal, answer)

        if proposal.operation == "send":
            record = self.executions.execute_once(
                action, lambda: self.gmail.send(proposal).model_dump(mode="json")
            )
            success = "Sent." if record.status == "verified" else self._failure_message(record)
        else:
            record = self.executions.execute_once(
                action, lambda: self.gmail.save_draft(proposal).model_dump(mode="json")
            )
            success = "Draft saved." if record.status == "verified" else self._failure_message(record)
        return success, record

    def _calendar_workflow(
        self,
        text: str,
        state: JarvisState,
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]], ExecutionRecord | None]:
        lowered = text.lower()
        if "create" in lowered or lowered.startswith("add "):
            proposal = self._create_event_proposal(text, messages)
            if proposal is None:
                return "Very well. I haven’t created anything.", state.get("recent_events", []), None
            action = ProposedAction(
                tool_name="calendar.create_event",
                risk=RiskLevel.EXTERNAL_WRITE,
                summary=f"Create event: {proposal.title}",
                payload=proposal.model_dump(mode="json"),
                idempotency_key=f"{self._task_context.get()}:create_event",
            )
            answer = self._ask(
                messages,
                kind="approval",
                prompt=self._format_calendar_approval(proposal),
                action=action,
            )
            if not self._is_approval(answer):
                return "Very well. I haven’t created anything.", state.get("recent_events", []), None
            record = self.executions.execute_once(action, lambda: self._execute_calendar(proposal))
            if record.status != "verified":
                return self._failure_message(record), state.get("recent_events", []), record
            return f"Done. {proposal.title} has been added to your calendar.", [], record

        if not any(word in lowered for word in ("move", "reschedul", "cancel", "create", "add")):
            if "free" in lowered or "available" in lowered:
                start = self._next_monday(date.today()) if "next week" in lowered else date.today()
                slots = self.calendar_agent.free_slots(start, limit=3)
                if not slots:
                    return "I couldn’t find a suitable free slot in that range.", [], None
                labels = ", ".join(
                    slot.strftime("%A at %I:%M %p").replace(" 0", " ") for slot in slots
                )
                return f"You’re free {labels}.", [], None
            day = self._date_from_text(text) or date.today()
            events = self.calendar_agent.events_for_day(day)
            response = self._summarize_schedule(day, events)
            return response, [event.model_dump(mode="json") for event in events], None

        prior = [CalendarEvent.model_validate(event) for event in state.get("recent_events", [])]
        reference = self._event_reference(text)
        candidates = self.calendar_agent.find_events(reference, prior or None)
        if not candidates:
            return f"I couldn’t find an event matching “{reference}”.", state.get("recent_events", []), None
        if len(candidates) > 1:
            choices = "\n".join(
                f"{index + 1}. {event.title} at {event.start:%A %I:%M %p}"
                for index, event in enumerate(candidates)
            )
            answer = self._ask(
                messages,
                kind="clarification",
                prompt=f"Which event did you mean?\n{choices}",
            )
            if self._is_rejection(answer):
                return "Very well. I haven’t changed anything.", state.get("recent_events", []), None
            event = self._select_event(answer, candidates)
            if event is None:
                return "I couldn’t identify the event, so I haven’t changed anything.", state.get("recent_events", []), None
        else:
            event = candidates[0]

        if "cancel" in lowered:
            proposal = self.calendar_agent.cancel_proposal(event)
            risk = RiskLevel.DESTRUCTIVE
        else:
            target_day = self._date_from_text(text, future=True)
            if target_day is None:
                answer = self._ask(
                    messages,
                    kind="clarification",
                    prompt="What day would you like to move it to?",
                )
                if self._is_rejection(answer):
                    return "Very well. I haven’t changed anything.", state.get("recent_events", []), None
                target_day = self._date_from_text(answer, future=True)
            if target_day is None:
                return "I couldn’t establish the date, so I haven’t changed anything.", state.get("recent_events", []), None
            target_time = self._time_from_text(text)
            if target_time is None:
                free = self.calendar_agent.free_slots(target_day, days=1, limit=4)
                if not free:
                    return "I couldn’t find an available time that day.", state.get("recent_events", []), None
                choices = " or ".join(slot.strftime("%I:%M %p").lstrip("0") for slot in free)
                answer = self._ask(
                    messages,
                    kind="clarification",
                    prompt=f"You’re free at {choices}. Which would you prefer?",
                )
                if self._is_rejection(answer):
                    return "Very well. I haven’t changed anything.", state.get("recent_events", []), None
                target_time = self._time_from_text(answer)
            if target_time is None:
                return "I couldn’t establish the time, so I haven’t changed anything.", state.get("recent_events", []), None
            proposal = self.calendar_agent.update_proposal(event, datetime.combine(target_day, target_time))
            risk = RiskLevel.EXTERNAL_WRITE

        action = ProposedAction(
            tool_name=f"calendar.{proposal.operation}",
            risk=risk,
            summary=f"{proposal.operation.replace('_', ' ').title()}: {proposal.title}",
            payload=proposal.model_dump(mode="json"),
            idempotency_key=f"{self._task_context.get()}:{proposal.operation}",
        )
        answer = self._ask(
            messages,
            kind="approval",
            prompt=self._format_calendar_approval(proposal),
            action=action,
        )
        if not self._is_approval(answer):
            return "Very well. I haven’t changed anything.", state.get("recent_events", []), None

        record = self.executions.execute_once(action, lambda: self._execute_calendar(proposal))
        if record.status != "verified":
            return self._failure_message(record), state.get("recent_events", []), record
        verb = "cancelled" if proposal.operation == "cancel_event" else "updated"
        if any(marker in answer.lower() for marker in ("email", "send him", "send her", "send them")):
            email_result, email_record = self._email_after_calendar(event, answer, messages)
            if email_result == "Sent.":
                return (
                    f"Done. {proposal.title} has been {verb}, and the email has been sent.",
                    [],
                    email_record,
                )
            return (
                f"{proposal.title} has been {verb}. {email_result}",
                [],
                email_record or record,
            )
        return f"Done. {proposal.title} has been {verb}.", [], record

    def _email_after_calendar(
        self,
        event: CalendarEvent,
        instruction: str,
        messages: list[dict[str, str]],
    ) -> tuple[str, ExecutionRecord | None]:
        contact: ResolvedContact | None = None
        for attendee in event.attendees:
            matches = self.email_agent.resolve_contact(attendee)
            if len(matches) == 1:
                contact = matches[0]
                break
        if contact is None:
            name_match = re.search(r"with\s+(.+)$", event.title, re.I)
            contact = self._resolve_contact(name_match.group(1) if name_match else None, messages)
        if contact is None:
            return "I couldn’t establish whom to email, so I haven’t sent anything.", None
        outcome = (
            "apologize for moving our meeting"
            if "apolog" in instruction.lower() or "sorry" in instruction.lower()
            else "let you know that our meeting has been updated"
        )
        proposal = self.email_agent.compose(
            EmailRequest(
                operation="send",
                recipient_name=contact.name,
                recipient_email=contact.email,
                desired_outcome=outcome,
            ),
            contact,
        )
        return self._execute_email_proposal(proposal, messages)

    def _create_event_proposal(
        self, text: str, messages: list[dict[str, str]]
    ) -> CalendarProposal | None:
        title_match = re.search(
            r"(?:create|add)\s+(?:an?\s+event\s+)?(.+?)(?:\s+(?:today|tomorrow|on\s+"
            r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))|\s+at\s+|$)",
            text,
            re.I,
        )
        title = title_match.group(1).strip().title() if title_match else "New event"
        day = self._date_from_text(text, future=True)
        if day is None:
            answer = self._ask(
                messages,
                kind="clarification",
                prompt=f"What day should I add {title}?",
            )
            if self._is_rejection(answer):
                return None
            day = self._date_from_text(answer, future=True)
            text = f"{text} {answer}"
        start_time = self._time_from_text(text)
        if start_time is None:
            answer = self._ask(
                messages,
                kind="clarification",
                prompt=f"What time should {title} start?",
            )
            if self._is_rejection(answer):
                return None
            start_time = self._time_from_text(answer)
        if day is None or start_time is None:
            return None
        start = datetime.combine(day, start_time)
        return CalendarProposal(
            operation="create_event",
            title=title,
            start=start,
            end=start + timedelta(hours=1),
            attendees=[],
        )

    def _execute_calendar(self, proposal: CalendarProposal) -> dict[str, Any]:
        if proposal.operation == "update_event":
            result = self.calendar.update_event(proposal)
        elif proposal.operation == "cancel_event":
            result = self.calendar.cancel_event(proposal)
        else:
            result = self.calendar.create_event(proposal)
        return result.model_dump(mode="json")

    @staticmethod
    def _format_email_approval(proposal: EmailProposal) -> str:
        verb = "send" if proposal.operation == "send" else "save this draft"
        return (
            f"To: {proposal.recipient_name} <{proposal.recipient_email}>\n"
            f"Subject: {proposal.subject}\n\n{proposal.body}\n\nShall I {verb}?"
        )

    @staticmethod
    def _format_calendar_approval(proposal: CalendarProposal) -> str:
        if proposal.operation == "cancel_event":
            return f"Cancel {proposal.title} on {proposal.start:%A, %B %d at %I:%M %p}?"
        if proposal.operation == "create_event":
            return (
                f"Create {proposal.title} on {proposal.start:%A, %B %d at %I:%M %p} "
                f"until {proposal.end:%I:%M %p}?"
            )
        return (
            f"Move {proposal.title} to {proposal.start:%A, %B %d at %I:%M %p} "
            f"until {proposal.end:%I:%M %p}?"
        )

    @staticmethod
    def _summarize_schedule(day: date, events: list[CalendarEvent]) -> str:
        label = "Tomorrow" if day == date.today() + timedelta(days=1) else day.strftime("%A, %B %d")
        if not events:
            return f"{label} is clear. A suspiciously civilized day."
        items = ", ".join(
            f"{event.title} at {event.start.strftime('%I:%M %p').lstrip('0')}" for event in events
        )
        return f"{label}, you have {len(events)} event{'s' if len(events) != 1 else ''}: {items}."

    @staticmethod
    def _event_reference(text: str) -> str:
        lowered = text.lower()
        for marker in ("dinner", "david", "stand-up", "standup"):
            if marker in lowered:
                return marker
        match = re.search(r"(?:move|reschedule|cancel)\s+(?:the\s+)?(.+?)(?:\s+to\s+|$)", text, re.I)
        return match.group(1).strip() if match else "meeting"

    @staticmethod
    def _date_from_text(text: str, *, future: bool = False) -> date | None:
        lowered = text.lower()
        today = date.today()
        if "tomorrow" in lowered:
            return today + timedelta(days=1)
        if "today" in lowered:
            return today
        for name, weekday in WEEKDAYS.items():
            if name in lowered:
                delta = (weekday - today.weekday()) % 7
                if delta == 0 and (future or "next" in lowered):
                    delta = 7
                if "next" in lowered and delta < 7:
                    delta += 7
                return today + timedelta(days=delta)
        return None

    @staticmethod
    def _time_from_text(text: str) -> time | None:
        lowered = text.lower()
        word_hours = {
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "noon": 12,
            "one": 13,
            "two": 14,
            "three": 15,
            "four": 16,
            "five": 17,
            "six": 18,
            "seven": 19,
        }
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered)
        if match:
            hour = int(match.group(1)) % 12 + (12 if match.group(3) == "pm" else 0)
            return time(hour, int(match.group(2) or 0))
        match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lowered)
        if match:
            return time(int(match.group(1)), int(match.group(2)))
        for word, hour in word_hours.items():
            if re.search(rf"\b{word}\b", lowered):
                return time(hour)
        return None

    @staticmethod
    def _select_choice(answer: str, choices: list[ResolvedContact]) -> ResolvedContact | None:
        lowered = Jarvis._contact_query_from_answer(answer).lower()
        for index, choice in enumerate(choices, start=1):
            if str(index) == lowered.strip() or choice.name.lower() in lowered or choice.email.lower() in lowered:
                return choice
        return None

    @staticmethod
    def _contact_query_from_answer(answer: str) -> str:
        """Extract a corrected name or email from a natural clarification response."""
        text = answer.strip()
        email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        if email:
            return email.group(0)

        spelled = re.search(
            r"(?<![A-Za-z])([A-Za-z](?:\s*[-.]\s*[A-Za-z]){1,})(?![A-Za-z])",
            text,
        )
        if spelled:
            return "".join(re.findall(r"[A-Za-z]", spelled.group(1)))

        spaced_letters = re.search(
            r"(?:^|\s)((?:[A-Za-z]\s+){1,}[A-Za-z])(?:[.!?,]|$)",
            text,
        )
        if spaced_letters:
            return "".join(spaced_letters.group(1).split())

        cleaned = re.sub(
            r"^(?:no[,.]?\s+)?(?:i\s+meant(?:\s+(?:to\s+)?say)?|"
            r"it(?:'s|\s+is)|that(?:'s|\s+is)|the\s+name\s+is|"
            r"their\s+name\s+is|and\s+then|say|spelled?)\s+",
            "",
            text,
            flags=re.I,
        ).strip(" \t\r\n.,!?\"'")
        cleaned = re.sub(r"^(?:to|two|say)\s+(?=\S+$)", "", cleaned, flags=re.I)

        repeated = [part.strip() for part in re.split(r"[,;]", cleaned) if part.strip()]
        if len(repeated) > 1 and len({part.lower() for part in repeated}) == 1:
            return repeated[0]
        return cleaned

    @staticmethod
    def _select_event(answer: str, choices: list[CalendarEvent]) -> CalendarEvent | None:
        lowered = answer.lower().strip()
        if lowered.isdigit() and 1 <= int(lowered) <= len(choices):
            return choices[int(lowered) - 1]
        for event in choices:
            if event.title.lower() in lowered:
                return event
        return None

    @staticmethod
    def _is_approval(text: str) -> bool:
        lowered = text.lower().strip()
        return any(word in lowered for word in APPROVE_WORDS) and not Jarvis._is_rejection(text)

    @staticmethod
    def _is_rejection(text: str) -> bool:
        lowered = text.lower().strip()
        return any(
            lowered == word or lowered.startswith(f"{word} ") or word in lowered
            for word in REJECT_WORDS
        )

    @staticmethod
    def _next_monday(day: date) -> date:
        return day + timedelta(days=(7 - day.weekday()))

    @staticmethod
    def _failure_message(record: ExecutionRecord) -> str:
        error = (record.result or {}).get("error", "the provider did not confirm success")
        return f"I couldn’t complete that action: {error}."
