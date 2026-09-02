def test_provider_modules_import_independently() -> None:
    from jarvis.integrations.calendar import MockCalendarProvider
    from jarvis.integrations.contacts import StaticContactProvider
    from jarvis.integrations.gmail import MockGmailProvider

    assert MockCalendarProvider(events=[]).events == []
    assert StaticContactProvider(contacts=[]).contacts == []
    assert MockGmailProvider().sent == []
