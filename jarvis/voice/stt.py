from __future__ import annotations

from typing import Any


class PushToTalkSTT:
    def __init__(self, model_size: str = "small") -> None:
        try:
            import sounddevice as sd
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install the 'voice' extras to use speech recognition") from exc
        self.sd = sd
        self.sample_rate = 16000
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def listen(self) -> str:
        import numpy as np

        input("Press Enter to start speaking, then press Enter again to stop...")
        frames: list[Any] = []

        def callback(indata, *_):
            frames.append(indata.copy())

        stream = self.sd.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback
        )
        with stream:
            input()
        if not frames:
            return ""
        audio = np.concatenate(frames, axis=0).flatten()
        segments, _ = self.model.transcribe(audio, language="en", beam_size=5)
        return " ".join(segment.text for segment in segments).strip()

