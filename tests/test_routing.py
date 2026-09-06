from jarvis.core.router import Route, Router
from jarvis import Jarvis
from jarvis.config import Settings


class CountingModel:
    def __init__(self) -> None:
        self.respond_calls = 0
        self.classify_calls = 0

    def respond(self, text, history):
        self.respond_calls += 1
        return "Online."

    def classify(self, text, history):
        self.classify_calls += 1
        return "conversation"


def test_routes_conversation_email_calendar_and_cross_domain() -> None:
    router = Router()

    assert router.route("I had a terrible interview") is Route.CONVERSATION
    assert router.route("Email David about next week's meeting") is Route.EMAIL
    assert router.route("What's on my calendar tomorrow?") is Route.CALENDAR
    assert (
        router.route("Find when I'm free next week and email David two options")
        is Route.CROSS_DOMAIN
    )


def test_fast_route_only_handles_explicit_actions() -> None:
    router = Router()

    assert router.explicit_route("Are you online?") is None
    assert router.explicit_route("I hate Monday meetings") is None
    assert router.explicit_route("Email David about lunch") is Route.EMAIL
    assert router.explicit_route("Move the dinner meeting to Friday") is Route.CALENDAR
    assert (
        router.explicit_route("Find when I'm free next week and email David")
        is Route.CROSS_DOMAIN
    )
    assert router.may_need_model_routing("Could you let David know?")
    assert not router.may_need_model_routing("Tell me something interesting")


def test_ordinary_conversation_skips_model_classification() -> None:
    model = CountingModel()
    jarvis = Jarvis(settings=Settings(), conversation_model=model)

    result = jarvis.turn("Are you online?", thread_id="fast-conversation")

    assert result.response == "Online."
    assert model.respond_calls == 1
    assert model.classify_calls == 0
    jarvis.close()
