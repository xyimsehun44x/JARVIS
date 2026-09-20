from __future__ import annotations

import re
from contextlib import nullcontext
from queue import Queue
from threading import Lock, Thread
from typing import Any

from jarvis.core.latency import LatencyRecorder


_END = object()


class KokoroTTS:
    STREAMING_MAXIMUM_CHARACTERS = 180

    def __init__(
        self,
        voice: str = "bm_george",
        latency_recorder: LatencyRecorder | None = None,
    ) -> None:
        try:
            import sounddevice as sd
            from kokoro import KPipeline
        except ImportError as exc:
            raise RuntimeError("Install the 'voice' extras to use speech synthesis") from exc
        self.sd = sd
        self.voice = voice
        self.latency = latency_recorder or LatencyRecorder()
        with self.latency.measure("tts.load"):
            self.pipeline = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M")

    def speak(self, text: str, *, response_started_at: float | None = None) -> None:
        speech = self.start_stream(
            response_started_at=response_started_at,
            maximum_characters=self.STREAMING_MAXIMUM_CHARACTERS,
            track_first_chunk_ready=False,
            discard_incomplete_tail=False,
        )
        speech.write(text)
        speech.finish()

    def start_stream(
        self,
        *,
        response_started_at: float | None = None,
        maximum_characters: int = STREAMING_MAXIMUM_CHARACTERS,
        track_first_chunk_ready: bool = True,
        discard_incomplete_tail: bool = True,
    ) -> KokoroSpeechStream:
        """Create a sentence-buffered, synthesis/playback-overlapped stream."""
        return KokoroSpeechStream(
            self,
            response_started_at=response_started_at,
            maximum_characters=maximum_characters,
            track_first_chunk_ready=track_first_chunk_ready,
            discard_incomplete_tail=discard_incomplete_tail,
        )

    @staticmethod
    def speech_chunks(text: str, *, maximum_characters: int = 90) -> list[str]:
        """Create short natural units so Kokoro can begin playback sooner."""
        spoken = re.sub(
            r"\s*Source:\s*[^.]+(?:\.\s*|$)",
            " ",
            text,
            flags=re.I,
        ).strip()
        sentences = re.split(r"(?<=[.!?])\s+|\n+", spoken)
        chunks: list[str] = []
        for sentence in sentences:
            remaining = sentence.strip()
            while len(remaining) > maximum_characters:
                split_at = remaining.rfind(" ", 0, maximum_characters + 1)
                if split_at < maximum_characters // 2:
                    split_at = maximum_characters
                chunks.append(remaining[:split_at].strip())
                remaining = remaining[split_at:].strip()
            if remaining:
                chunks.append(remaining)
        return chunks


class KokoroSpeechStream:
    """Queue model deltas while separate workers synthesize and play speech."""

    def __init__(
        self,
        tts: KokoroTTS,
        *,
        response_started_at: float | None = None,
        maximum_characters: int = KokoroTTS.STREAMING_MAXIMUM_CHARACTERS,
        track_first_chunk_ready: bool = True,
        discard_incomplete_tail: bool = True,
    ) -> None:
        self.tts = tts
        self.response_started_at = response_started_at
        self.maximum_characters = maximum_characters
        self.track_first_chunk_ready = track_first_chunk_ready
        self.discard_incomplete_tail = discard_incomplete_tail
        self._text_buffer = ""
        self._text_queue: Queue[str | object] = Queue()
        self._audio_queue: Queue[Any] = Queue()
        self._threads: tuple[Thread, Thread] | None = None
        self._errors: list[BaseException] = []
        self._error_lock = Lock()
        self._first_audio_recorded = False
        self._stream_started_at = tts.latency.now()
        self._tts_started_at: float | None = None
        self._trace_id = tts.latency.current_trace_id
        self._thread_id = tts.latency.current_thread_id
        self._closed = False
        self._enqueued_chunks = 0

    def write(self, delta: str) -> None:
        if self._closed:
            raise RuntimeError("Cannot write to a closed speech stream")
        if not delta:
            return
        self._text_buffer += delta
        for chunk in self._ready_chunks():
            self._enqueue(chunk)

    def finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        if (
            self.discard_incomplete_tail
            and self._enqueued_chunks
            and self._text_buffer.strip()
            and not re.search(
                r"[.!?][\"'\u2019\u201d)]*$", self._text_buffer.strip()
            )
        ):
            self._text_buffer = ""
            self.tts.latency.record("tts.incomplete_tail_discarded", 0.0)
        for chunk in self._ready_chunks(final=True):
            self._enqueue(chunk)
        if self._threads is None:
            return
        self._text_queue.put(_END)
        for thread in self._threads:
            thread.join()
        if self._tts_started_at is not None:
            self._record_elapsed("tts.total", self._tts_started_at)
        if self._errors:
            raise RuntimeError("Speech synthesis or playback failed") from self._errors[0]

    def abort(self) -> None:
        """Stop accepting text and let already-started worker activity unwind."""
        if self._closed:
            return
        self._closed = True
        self._text_buffer = ""
        if self._threads is not None:
            self._text_queue.put(_END)
            for thread in self._threads:
                thread.join()

    def _ready_chunks(self, *, final: bool = False) -> list[str]:
        ready: list[str] = []
        while self._text_buffer:
            sentence = re.search(
                r"[.!?][\"'\u2019\u201d)]*(?:\s+|$)", self._text_buffer
            )
            if sentence and sentence.end() <= self.maximum_characters:
                end = sentence.end()
            elif len(self._text_buffer) > self.maximum_characters:
                end = self._clause_boundary(self._text_buffer)
                if not end:
                    break
            elif final:
                end = len(self._text_buffer)
            else:
                break

            segment = self._text_buffer[:end].strip()
            self._text_buffer = self._text_buffer[end:]
            if segment:
                ready.extend(
                    KokoroTTS.speech_chunks(
                        segment, maximum_characters=self.maximum_characters
                    )
                )
        return ready

    def _clause_boundary(self, text: str) -> int:
        window = text[: self.maximum_characters + 1]
        minimum = self.maximum_characters // 2
        punctuation = [
            match.end()
            for match in re.finditer(
                r"[,;:]\s+|\s*(?:\u2013|\u2014)\s*|\s+-\s+", window
            )
        ]
        candidates = [position for position in punctuation if position >= minimum]
        if candidates:
            return candidates[-1]
        split_at = window.rfind(" ")
        return split_at + 1 if split_at >= minimum else self.maximum_characters

    def _enqueue(self, chunk: str) -> None:
        self._enqueued_chunks += 1
        if self._threads is None:
            chunk_ready_at = self.tts.latency.now()
            if self.track_first_chunk_ready:
                self.tts.latency.record_elapsed(
                    "tts.first_chunk_ready", self._stream_started_at
                )
            self._tts_started_at = chunk_ready_at
            synthesis = Thread(
                target=self._synthesis_worker,
                name="jarvis-tts-synthesis",
                daemon=True,
            )
            playback = Thread(
                target=self._playback_worker,
                name="jarvis-tts-playback",
                daemon=True,
            )
            self._threads = (synthesis, playback)
            synthesis.start()
            playback.start()
        self._text_queue.put(chunk)

    def _synthesis_worker(self) -> None:
        import numpy as np

        try:
            with self._latency_context():
                while True:
                    chunk = self._text_queue.get()
                    if chunk is _END:
                        break
                    with self.tts.latency.measure("tts.synthesis"):
                        for _, _, audio in self.tts.pipeline(
                            chunk, voice=self.tts.voice
                        ):
                            self._audio_queue.put(np.asarray(audio))
        except BaseException as exc:
            self._remember_error(exc)
        finally:
            self._audio_queue.put(_END)

    def _playback_worker(self) -> None:
        import numpy as np

        try:
            with self._latency_context():
                output_factory = getattr(self.tts.sd, "OutputStream", None)
                output_context = (
                    output_factory(samplerate=24000, channels=1, dtype="float32")
                    if output_factory
                    else nullcontext(None)
                )
                with output_context as output:
                    while True:
                        audio = self._audio_queue.get()
                        if audio is _END:
                            break
                        if not self._first_audio_recorded:
                            if self._tts_started_at is not None:
                                self.tts.latency.record_elapsed(
                                    "tts.first_audio", self._tts_started_at
                                )
                            if self.response_started_at is not None:
                                self.tts.latency.record_elapsed(
                                    "voice.time_to_first_response_audio",
                                    self.response_started_at,
                                )
                            self._first_audio_recorded = True
                        with self.tts.latency.measure("tts.playback"):
                            if output is not None:
                                samples = np.asarray(audio, dtype=np.float32).reshape(-1, 1)
                                output.write(samples)
                            else:
                                self.tts.sd.play(audio, samplerate=24000)
                                self.tts.sd.wait()
        except BaseException as exc:
            self._remember_error(exc)

    def _latency_context(self):
        if self._trace_id:
            return self.tts.latency.trace(
                self._trace_id, thread_id=self._thread_id
            )
        return nullcontext()

    def _record_elapsed(self, stage: str, started: float) -> None:
        with self._latency_context():
            self.tts.latency.record_elapsed(stage, started)

    def _remember_error(self, exc: BaseException) -> None:
        with self._error_lock:
            self._errors.append(exc)
