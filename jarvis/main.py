from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from jarvis.config import Settings
from jarvis.core.jarvis import Jarvis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jarvis V0.1")
    parser.add_argument("--text", action="store_true", help="Use the text interface (default)")
    parser.add_argument("--voice", action="store_true", help="Use push-to-talk STT and Kokoro TTS")
    parser.add_argument("--thread-id", default="default", help="Persistent conversation ID")
    parser.add_argument(
        "--setup-google", action="store_true", help="Run the Google desktop OAuth flow and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="Show configuration readiness without secrets"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    if args.check:
        _print_readiness(settings)
        return
    if args.setup_google:
        _setup_google(settings)
        return
    errors = settings.configuration_errors()
    if errors:
        raise SystemExit("Configuration error:\n- " + "\n- ".join(errors))

    jarvis = Jarvis(settings=settings)
    thread_id = args.thread_id
    stt = tts = None
    if args.voice:
        from jarvis.voice.stt import PushToTalkSTT
        from jarvis.voice.tts import KokoroTTS

        stt = PushToTalkSTT(
            model_size=settings.stt_model,
            beam_size=settings.stt_beam_size,
            hotwords=settings.stt_hotwords,
        )
        tts = KokoroTTS()

    print(f"Jarvis ready ({settings.mode} mode, thread: {thread_id}). Type 'quit' to leave.")
    try:
        while True:
            user_text = stt.listen() if stt else input("You: ").strip()
            if stt:
                print(f'Heard: "{user_text}"')
            if user_text.lower() in {"quit", "exit"}:
                break
            if not user_text:
                continue
            try:
                result = jarvis.turn(user_text, thread_id=thread_id)
                while result.needs_input:
                    print(f"Jarvis: {result.prompt}")
                    answer = stt.listen() if stt else input("You: ").strip()
                    if stt:
                        print(f'Heard: "{answer}"')
                    result = jarvis.resume(answer, thread_id=thread_id)
            except Exception as exc:
                response = _friendly_runtime_error(exc)
                print(f"Jarvis: {response}")
                if tts:
                    tts.speak(response)
                continue
            print(f"Jarvis: {result.response}")
            if tts and result.response:
                tts.speak(result.response)
    finally:
        jarvis.close()


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
