from __future__ import annotations

import re
from enum import Enum


class Route(str, Enum):
    CONVERSATION = "conversation"
    EMAIL = "email"
    CALENDAR = "calendar"
    CROSS_DOMAIN = "cross_domain"
    WEATHER = "weather"
    UNSUPPORTED_FRESH_DATA = "unsupported_fresh_data"


class Router:
    WEATHER_PATTERN = re.compile(
        r"\b(?:weather|forecast|temperature|rain(?:ing|y)?|snow(?:ing|y)?)\b",
        re.I,
    )
    UNSUPPORTED_FRESH_PATTERN = re.compile(
        r"\b(?:news|headlines|stock\s+price|share\s+price|bitcoin\s+price|"
        r"crypto\s+price|exchange\s+rate|sports?\s+score|live\s+score|traffic|"
        r"air\s+quality)\b",
        re.I,
    )
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

    def explicit_route(self, text: str) -> Route | None:
        """Return an unambiguous route without spending a model round trip."""
        lowered = text.lower()
        if self.WEATHER_PATTERN.search(text):
            return Route.WEATHER
        if self.UNSUPPORTED_FRESH_PATTERN.search(text):
            return Route.UNSUPPORTED_FRESH_DATA
        email = bool(re.search(r"\b(?:email|e-mail|mail)\b", lowered))
        calendar = any(
            marker in lowered
            for marker in (
                "calendar",
                "my schedule",
                "tomorrow looking",
                "am i free",
                "when am i free",
                "when i'm free",
                "when i am free",
                "free next",
                "available next",
            )
        ) or bool(
            re.search(
                r"\b(?:create|add|book|move|reschedule|cancel)\b.*"
                r"\b(?:meeting|event|lunch|dinner|appointment|call)\b",
                lowered,
            )
        )
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
        return None

    @staticmethod
    def may_need_model_routing(text: str) -> bool:
        """Identify possible implicit actions that still deserve semantic routing."""
        lowered = text.lower().strip()
        if re.search(r"\btell\s+(?:him|her|them|[A-Z][a-z]+)\b", text):
            return True
        return bool(
            re.search(
                r"^(?:please\s+)?(?:can you|could you|would you|i need you to|"
                r"send|draft|write|compose|book|schedule|move|reschedule|cancel|"
                r"check|find|remind)\b",
                lowered,
            )
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
