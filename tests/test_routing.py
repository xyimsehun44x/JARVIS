from jarvis.core.router import Route, Router


def test_routes_conversation_email_calendar_and_cross_domain() -> None:
    router = Router()

    assert router.route("I had a terrible interview") is Route.CONVERSATION
    assert router.route("Email David about next week's meeting") is Route.EMAIL
    assert router.route("What's on my calendar tomorrow?") is Route.CALENDAR
    assert (
        router.route("Find when I'm free next week and email David two options")
        is Route.CROSS_DOMAIN
    )

