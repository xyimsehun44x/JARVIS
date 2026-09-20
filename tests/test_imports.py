def test_provider_modules_import_independently() -> None:
    from jarvis.core.latency import LatencyRecorder
    from jarvis.integrations.calendar import MockCalendarProvider
    from jarvis.integrations.contacts import StaticContactProvider
    from jarvis.integrations.gmail import MockGmailProvider
    from jarvis.integrations.weather import MockWeatherProvider
    from jarvis.voice.stt import PushToTalkSTT, STTResult
    from jarvis.voice.tts import KokoroTTS
    from jarvis.voice.vocabulary import VocabularyStore

    assert LatencyRecorder().events_since() == []
    assert MockCalendarProvider(events=[]).events == []
    assert StaticContactProvider(contacts=[]).contacts == []
    assert MockGmailProvider().sent == []
    assert MockWeatherProvider().requests == []
    assert PushToTalkSTT.__name__ == "PushToTalkSTT"
    assert STTResult.__name__ == "STTResult"
    assert KokoroTTS.__name__ == "KokoroTTS"
    assert VocabularyStore().entries() == []
