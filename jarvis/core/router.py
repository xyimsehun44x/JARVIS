from __future__ import annotations

from enum import Enum


class Route(str, Enum):
    CONVERSATION = "conversation"
    EMAIL = "email"
    CALENDAR = "calendar"
    CROSS_DOMAIN = "cross_domain"


class Router:
    EMAIL_WORDS = ("email", "e-mail", "mail ", "send him", "send her")
    CALENDAR_WORDS = (
        "calendar",
        "schedule",
        "meeting",
        "event",
        "free ",
        "available",
        "tomorrow looking",
        "move the",
        "reschedule",
        "cancel the",
        "create an event",
        "add an event",
        "add lunch",
        "add dinner",
        "add meeting",
    )

    def route(self, text: str) -> Route:
        lowered = text.lower()
        email = any(word in lowered for word in self.EMAIL_WORDS)
        calendar = any(word in lowered for word in self.CALENDAR_WORDS)
        cross_domain = email and any(
            marker in lowered
            for marker in (
                "when i'm free",
                "when i am free",
                "free next",
                "available next",
                "check my calendar",
                "check my schedule",
                "find a time",
                "find when",
            )
        )
        if cross_domain:
            return Route.CROSS_DOMAIN
        if email:
            return Route.EMAIL
        if calendar:
            return Route.CALENDAR
        return Route.CONVERSATION
