from jarvis.voice.normalizer import SpeechNormalizer


def test_clean_transcript_is_unchanged() -> None:
    result = SpeechNormalizer().normalize("Tell me something interesting.")

    assert result.normalized_text == "Tell me something interesting."
    assert result.corrections == []
    assert not result.requires_clarification


def test_observed_tell_command_substitution_is_repaired_conservatively() -> None:
    repaired = SpeechNormalizer().normalize("Help me something interesting.")
    real_help = SpeechNormalizer().normalize("Help me with something interesting.")

    assert repaired.normalized_text == "Tell me something interesting."
    assert repaired.corrections[0].reason == "tell command substitution"
    assert real_help.normalized_text == "Help me with something interesting."


def test_weather_homophones_are_repaired_only_with_weather_context() -> None:
    normalizer = SpeechNormalizer()

    first = normalizer.normalize("Will it rain and soul moral?")
    second = normalizer.normalize("fell it rain and soul tomorrow")

    assert first.normalized_text == "Will it rain in Seoul tomorrow?"
    assert second.normalized_text == "Will it rain in Seoul tomorrow"
    assert len(first.corrections) == 2
    assert len(second.corrections) == 2


def test_calendar_read_phrase_can_be_repaired_without_mutating_entities() -> None:
    normalizer = SpeechNormalizer()

    repaired = normalizer.normalize("moral-looking life")
    contact = normalizer.normalize("Email Drew and say hello.")

    assert repaired.normalized_text == "What's tomorrow looking like?"
    assert contact.normalized_text == "Email Drew and say hello."


def test_email_command_homophone_preserves_the_recipient() -> None:
    result = SpeechNormalizer().normalize("female Joo and say hello")

    assert result.normalized_text == "Email Joo and say hello"
    assert result.corrections[0].original == "female"
    assert result.corrections[0].replacement == "Email"


def test_stt_quality_metadata_survives_normalization() -> None:
    result = SpeechNormalizer().normalize(
        "Email Joo and say hello",
        stt_confidence=0.42,
        rejection_reason="no_transcript",
    )

    assert result.stt_confidence == 0.42
    assert result.requires_clarification
    assert result.rejection_reason == "no_transcript"
