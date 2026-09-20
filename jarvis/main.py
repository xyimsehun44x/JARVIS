from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from uuid import uuid4

from jarvis.config import Settings
from jarvis.core.jarvis import Jarvis
from jarvis.core.latency import LatencyRecorder
from jarvis.core.session import SessionCoordinator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jarvis V0.1")
    parser.add_argument("--text", action="store_true", help="Use the text interface (default)")
    parser.add_argument("--voice", action="store_true", help="Use push-to-talk STT and Kokoro TTS")
    parser.add_argument(
        "--ipc-stdio",
        action="store_true",
        help="Serve versioned desktop IPC as newline-delimited JSON over stdio",
    )
    parser.add_argument(
        "--latency",
        action="store_true",
        help="Print structured per-turn and session latency summaries",
    )
    parser.add_argument(
        "--latency-report",
        metavar="PATH",
        help="Write a sanitized JSON latency report when the session ends",
    )
    parser.add_argument("--thread-id", default="default", help="Persistent conversation ID")
    parser.add_argument(
        "--setup-google", action="store_true", help="Run the Google desktop OAuth flow and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="Show configuration readiness without secrets"
    )
    vocabulary = parser.add_mutually_exclusive_group()
    vocabulary.add_argument(
        "--vocabulary-list",
        action="store_true",
        help="List explicit local speech vocabulary and exit",
    )
    vocabulary.add_argument(
        "--vocabulary-add",
        metavar="TERM",
        help="Add or update one explicit local speech vocabulary term and exit",
    )
    vocabulary.add_argument(
        "--vocabulary-remove",
        metavar="TERM",
        help="Remove one explicit local speech vocabulary term and exit",
    )
    parser.add_argument(
        "--vocabulary-alias",
        action="append",
        default=[],
        metavar="ALIAS",
        help="Alias for --vocabulary-add; may be repeated",
    )
    parser.add_argument(
        "--vocabulary-sensitive",
        action="store_true",
        help="Protect the added term from automatic replacement and STT bias",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    if args.vocabulary_alias and not args.vocabulary_add:
        raise SystemExit("--vocabulary-alias requires --vocabulary-add")
    if args.vocabulary_sensitive and not args.vocabulary_add:
        raise SystemExit("--vocabulary-sensitive requires --vocabulary-add")
    if args.vocabulary_list or args.vocabulary_add or args.vocabulary_remove:
        _manage_vocabulary(args, settings)
        return
    if args.check:
        _print_readiness(settings)
        return
    if args.setup_google:
        _setup_google(settings)
        return
    errors = settings.configuration_errors()
    if errors:
        raise SystemExit("Configuration error:\n- " + "\n- ".join(errors))
    if args.ipc_stdio:
        if args.voice or args.latency_report:
            raise SystemExit("--ipc-stdio cannot be combined with --voice or --latency-report")
        from jarvis.ipc.server import run_stdio_server

        run_stdio_server(settings)
        return

    latency = LatencyRecorder(
        enabled=args.latency or bool(args.latency_report) or settings.latency_logging
    )
    jarvis = Jarvis(settings=settings, latency_recorder=latency)
    session = SessionCoordinator(
        jarvis,
        speech_action_confidence_threshold=settings.stt_action_confidence_threshold,
    )
    thread_id = args.thread_id
    stt = tts = vocabulary_store = None
    speech_normalizer = None
    if args.voice:
        from jarvis.voice.normalizer import SpeechNormalizer
        from jarvis.voice.stt import PushToTalkSTT
        from jarvis.voice.tts import KokoroTTS
        from jarvis.voice.vocabulary import VocabularyStore

        vocabulary_store = VocabularyStore(settings.vocabulary_path)
        stt = PushToTalkSTT(
            model_size=settings.stt_model,
            beam_size=settings.stt_beam_size,
            hotwords=settings.stt_hotwords,
            initial_prompt=settings.stt_initial_prompt,
            vad_filter=settings.stt_vad_filter,
            latency_recorder=latency,
        )
        tts = KokoroTTS(latency_recorder=latency)
        speech_normalizer = SpeechNormalizer(vocabulary_store)

    print(f"Jarvis ready ({settings.mode} mode, thread: {thread_id}). Type 'quit' to leave.")
    try:
        while True:
            checkpoint = latency.checkpoint()
            trace_id = str(uuid4())
            should_exit = False
            with latency.trace(trace_id, thread_id=thread_id):
                if stt and speech_normalizer:
                    utterance = _listen_for_utterance(
                        stt,
                        speech_normalizer,
                        latency,
                        vocabulary=_thread_vocabulary(
                            jarvis,
                            vocabulary_store,
                            thread_id,
                            settings.stt_vocabulary_limit,
                        ),
                    )
                    raw_text = utterance.raw_transcript
                else:
                    raw_text = input("You: ").strip()
                    utterance = None
                user_text = utterance.normalized_text if utterance else raw_text
                if user_text.lower() in {"quit", "exit"}:
                    should_exit = True
                elif user_text or utterance:
                    response_audio = None
                    try:
                        result, response_audio = _session_turn(
                            session,
                            utterance or user_text,
                            thread_id=thread_id,
                            trace_id=trace_id,
                            tts=tts,
                            response_started_at=(
                                stt.last_capture_ended_at if stt else None
                            ),
                        )
                        while result.needs_input:
                            print(f"Jarvis: {result.prompt}")
                            _finish_response_audio(
                                result,
                                response_audio,
                                tts,
                                result.prompt,
                                response_started_at=(
                                    stt.last_capture_ended_at if stt else None
                                ),
                            )
                            if stt and speech_normalizer:
                                answer_utterance = _listen_for_utterance(
                                    stt,
                                    speech_normalizer,
                                    latency,
                                    vocabulary=_thread_vocabulary(
                                        jarvis,
                                        vocabulary_store,
                                        thread_id,
                                        settings.stt_vocabulary_limit,
                                    ),
                                )
                                answer = answer_utterance.normalized_text
                            else:
                                answer = input("You: ").strip()
                                answer_utterance = None
                            result, response_audio = _session_turn(
                                session,
                                answer_utterance or answer,
                                thread_id=thread_id,
                                trace_id=trace_id,
                                tts=tts,
                                response_started_at=(
                                    stt.last_capture_ended_at if stt else None
                                ),
                            )
                    except Exception as exc:
                        if response_audio:
                            response_audio.abort()
                        response = _friendly_runtime_error(exc)
                        print(f"Jarvis: {response}")
                        if tts:
                            tts.speak(
                                response,
                                response_started_at=stt.last_capture_ended_at if stt else None,
                            )
                    else:
                        print(f"Jarvis: {result.response}")
                        _finish_response_audio(
                            result,
                            response_audio,
                            tts,
                            result.response,
                            response_started_at=(
                                stt.last_capture_ended_at if stt else None
                            ),
                        )
            events = latency.events_since(checkpoint)
            if latency.enabled and events:
                print("Latency: " + latency.format_events(events))
            if should_exit:
                break
    except KeyboardInterrupt:
        print("\nJarvis stopped.")
    finally:
        if latency.enabled and latency.events_since():
            print("Latency session: " + latency.format_statistics())
        if args.latency_report:
            report_path = latency.write_report(
                args.latency_report,
                metadata={
                    "mode": settings.mode,
                    "llm_provider": settings.llm_provider,
                    "llm_model": (
                        settings.gemini_model
                        if settings.llm_provider == "gemini"
                        else settings.openai_model
                    ),
                    "stt_model": settings.stt_model,
                    "stt_beam_size": settings.stt_beam_size,
                    "stt_vad_filter": settings.stt_vad_filter,
                    "voice_mode": bool(args.voice),
                },
            )
            print(f"Latency report: {report_path}")
        try:
            if vocabulary_store:
                vocabulary_store.close()
        finally:
            jarvis.close()


def _listen_for_utterance(stt, speech_normalizer, latency, *, vocabulary=None):
    stt_result = stt.listen(vocabulary=vocabulary)
    with latency.measure("speech.normalize"):
        utterance = speech_normalizer.normalize(
            stt_result.text,
            stt_confidence=stt_result.confidence,
            rejection_reason=stt_result.rejection_reason,
        )
    print(f'Heard: "{stt_result.text}"')
    if utterance.normalized_text != stt_result.text:
        print(f'Understood: "{utterance.normalized_text}"')
    return utterance


def _session_turn(
    session,
    text,
    *,
    thread_id: str,
    trace_id: str,
    tts=None,
    response_started_at: float | None = None,
):
    response_audio = (
        tts.start_stream(response_started_at=response_started_at) if tts else None
    )
    try:
        result = session.turn(
            text,
            thread_id=thread_id,
            trace_id=trace_id,
            on_response_delta=(response_audio.write if response_audio else None),
            voice_response=response_audio is not None,
        )
    except Exception:
        if response_audio:
            response_audio.abort()
        raise
    return result, response_audio


def _finish_response_audio(
    result,
    response_audio,
    tts,
    text: str | None,
    *,
    response_started_at: float | None = None,
) -> None:
    if response_audio:
        if result.response_streamed:
            response_audio.finish()
        else:
            response_audio.abort()
    if tts and text and not result.response_streamed:
        tts.speak(text, response_started_at=response_started_at)


def _thread_vocabulary(jarvis, vocabulary_store, thread_id: str, limit: int) -> list[str]:
    if not vocabulary_store:
        return []
    messages = jarvis.state(thread_id=thread_id).get("messages", [])[-6:]
    context = " ".join(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict)
    )
    return vocabulary_store.select_terms(context, limit=limit)


def _manage_vocabulary(args, settings: Settings) -> None:
    from jarvis.voice.vocabulary import VocabularyStore

    store = VocabularyStore(settings.vocabulary_path)
    try:
        if args.vocabulary_add:
            entry = store.add(
                args.vocabulary_add,
                aliases=args.vocabulary_alias,
                sensitive=args.vocabulary_sensitive,
            )
            protection = "protected" if entry.sensitive else "automatic"
            print(
                f"Vocabulary saved: {entry.canonical} "
                f"({len(entry.aliases)} aliases, {protection})"
            )
            return
        if args.vocabulary_remove:
            removed = store.remove(args.vocabulary_remove)
            print(
                f"Vocabulary removed: {args.vocabulary_remove}"
                if removed
                else f"Vocabulary entry not found: {args.vocabulary_remove}"
            )
            return
        entries = store.entries()
        if not entries:
            print("Vocabulary is empty.")
            return
        for entry in entries:
            aliases = ", ".join(entry.aliases) if entry.aliases else "-"
            protection = "protected" if entry.sensitive else "automatic"
            print(
                f"{entry.canonical} | aliases: {aliases} | {protection} | "
                f"uses: {entry.usage_count}"
            )
    finally:
        store.close()


def _print_readiness(settings: Settings) -> None:
    print(f"Mode: {settings.mode}")
    model = (
        settings.gemini_model
        if settings.llm_provider == "gemini"
        else settings.openai_model
    )
    print(f"LLM: {settings.llm_provider} ({model})")
    print(f"OpenAI API key: {'configured' if settings.openai_api_key else 'missing'}")
    print(f"Gemini API key: {'configured' if settings.gemini_api_key else 'missing'}")
    print(
        "Google credentials: "
        + ("found" if Path(settings.google_credentials_path).is_file() else "missing")
    )
    print("Google token: " + ("found" if Path(settings.google_token_path).is_file() else "missing"))
    print(f"Persistence: {settings.persistence} ({settings.database_path})")
    print(
        "Weather: Open-Meteo"
        + (f" (default: {settings.default_location})" if settings.default_location else "")
    )
    print(f"Live Gmail send: {'enabled' if settings.allow_email_send else 'locked'}")
    print(
        f"Live Calendar writes: {'enabled' if settings.allow_calendar_writes else 'locked'}"
    )
    voice_modules = ("faster_whisper", "kokoro", "sounddevice")
    print(
        "Voice dependencies: "
        + (
            "installed"
            if all(importlib.util.find_spec(module) for module in voice_modules)
            else "missing"
        )
    )
    print(f"Voice STT: {settings.stt_model} (beam size {settings.stt_beam_size})")
    hotword_count = len([word for word in settings.stt_hotwords.split(",") if word.strip()])
    print(f"Voice name hints: {hotword_count} configured")
    print(f"Latency logging: {'enabled' if settings.latency_logging else 'disabled'}")
    errors = settings.configuration_errors()
    if errors:
        print("Not ready:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Configuration is ready.")


def _setup_google(settings: Settings) -> None:
    if settings.mode != "google":
        raise SystemExit("Set JARVIS_MODE=google before running Google setup.")
    if not Path(settings.google_credentials_path).is_file():
        raise SystemExit(
            f"Place your desktop OAuth client at {settings.google_credentials_path} first."
        )
    from jarvis.integrations.google_auth import GoogleOAuth, scopes_for_settings

    oauth = GoogleOAuth(
        settings.google_credentials_path,
        settings.google_token_path,
        scopes_for_settings(settings),
    )
    oauth.authorize(interactive=True)
    print(f"Google authorization saved to {settings.google_token_path}.")


def _friendly_runtime_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "quota" in message or "429" in message or "rate limit" in message:
        return "The Gemini service has reached its current rate limit. Please try again shortly."
    if "timeout" in message or "timed out" in message:
        return "The service took too long to respond. Please try that again."
    return "I encountered a service error, but the session is still running. Please try again."


if __name__ == "__main__":
    main()
