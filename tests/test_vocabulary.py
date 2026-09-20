import json
from types import SimpleNamespace

import pytest

from jarvis.config import Settings
from jarvis.main import _manage_vocabulary, build_parser
from jarvis.voice.normalizer import SpeechNormalizer
from jarvis.voice.stt import PushToTalkSTT
from jarvis.voice.vocabulary import VocabularyStore


def test_explicit_vocabulary_persists_and_normalizes_longest_alias_first(tmp_path) -> None:
    path = tmp_path / "vocabulary.json"
    store = VocabularyStore(path)
    store.add("Omega", aliases=["omega"])
    store.add("OmegaETH", aliases=["omega eth"])

    result = SpeechNormalizer(store).normalize("benchmark omega eth beside omega")
    store.close()

    assert result.normalized_text == "benchmark OmegaETH beside Omega"
    assert [correction.replacement for correction in result.corrections] == [
        "OmegaETH",
        "Omega",
    ]

    restored = VocabularyStore(path)
    assert {entry.canonical: entry.usage_count for entry in restored.entries()} == {
        "Omega": 1,
        "OmegaETH": 1,
    }


def test_vocabulary_replacement_cascade_is_rejected_at_definition_time(tmp_path) -> None:
    store = VocabularyStore(tmp_path / "vocabulary.json")
    store.add("Middle", aliases=["start"])

    with pytest.raises(ValueError, match="already belongs"):
        store.add("Finish", aliases=["middle"])


def test_sensitive_and_low_confidence_entries_are_never_automatic(tmp_path) -> None:
    store = VocabularyStore(tmp_path / "vocabulary.json")
    store.add("Joo Kim", aliases=["Jew Kim"], sensitive=True)
    store.add("Tentative", aliases=["tent a tive"], explicit=False, confidence=0.7)
    store.add("LangGraph", aliases=["lang graph"])

    normalized, corrections = store.apply("Jew Kim uses lang graph and tent a tive")
    selected = store.select_terms("Joo Kim and LangGraph", limit=15)

    assert normalized == "Jew Kim uses LangGraph and tent a tive"
    assert [correction.canonical for correction in corrections] == ["LangGraph"]
    assert selected == ["LangGraph"]


def test_ambiguous_aliases_are_rejected(tmp_path) -> None:
    store = VocabularyStore(tmp_path / "vocabulary.json")
    store.add("First", aliases=["shared phrase"])

    with pytest.raises(ValueError, match="already belongs"):
        store.add("Second", aliases=["shared phrase"])


def test_contextual_selection_is_prioritized_and_hard_capped(tmp_path) -> None:
    store = VocabularyStore(tmp_path / "vocabulary.json")
    for index in range(20):
        store.add(f"Project{index}", aliases=[f"project {index}"])
    for _ in range(3):
        store.apply("project 1")

    selected = store.select_terms("We are discussing project 19", limit=99)

    assert len(selected) == 15
    assert selected[0] == "Project19"
    assert "Project1" in selected


def test_vocabulary_file_schema_is_validated(tmp_path) -> None:
    path = tmp_path / "vocabulary.json"
    path.write_text(json.dumps({"schema_version": 999, "entries": []}), encoding="utf-8")

    try:
        VocabularyStore(path)
    except ValueError as exc:
        assert "schema version" in str(exc)
    else:
        raise AssertionError("unsupported vocabulary schema was accepted")


def test_stt_vocabulary_is_deduplicated_bounded_and_weakly_prompted() -> None:
    terms = PushToTalkSTT._bounded_vocabulary(
        ["OmegaETH", " omegaeth ", "LangGraph", *[f"Term {index}" for index in range(20)]]
    )
    stt = object.__new__(PushToTalkSTT)
    stt.initial_prompt = "Jarvis personal assistant"

    assert len(terms) == 15
    assert terms[:2] == ["OmegaETH", "LangGraph"]
    assert stt._prompt_with_vocabulary(terms).startswith("Jarvis personal assistant;")


def test_vocabulary_cli_parses_explicit_alias_and_protection() -> None:
    args = build_parser().parse_args(
        [
            "--vocabulary-add",
            "Joo Kim",
            "--vocabulary-alias",
            "Jew Kim",
            "--vocabulary-sensitive",
        ]
    )

    assert args.vocabulary_add == "Joo Kim"
    assert args.vocabulary_alias == ["Jew Kim"]
    assert args.vocabulary_sensitive


def test_vocabulary_cli_management_writes_explicit_entry(tmp_path, capsys) -> None:
    settings = Settings(vocabulary_path=str(tmp_path / "vocabulary.json"))
    args = SimpleNamespace(
        vocabulary_add="OmegaETH",
        vocabulary_alias=["omega eth"],
        vocabulary_sensitive=False,
    )

    _manage_vocabulary(args, settings)

    assert "Vocabulary saved: OmegaETH" in capsys.readouterr().out
    restored = VocabularyStore(settings.vocabulary_path)
    assert restored.entries()[0].aliases == ("omega eth",)


def test_vocabulary_entry_can_be_removed_and_stays_removed(tmp_path) -> None:
    path = tmp_path / "vocabulary.json"
    store = VocabularyStore(path)
    store.add("OmegaETH", aliases=["omega eth"])

    assert store.remove("omegaeth")
    assert not store.remove("missing")
    assert VocabularyStore(path).entries() == []
