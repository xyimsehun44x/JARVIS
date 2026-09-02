from __future__ import annotations


class KokoroTTS:
    def __init__(self, voice: str = "bm_george") -> None:
        try:
            import sounddevice as sd
            from kokoro import KPipeline
        except ImportError as exc:
            raise RuntimeError("Install the 'voice' extras to use speech synthesis") from exc
        self.sd = sd
        self.voice = voice
        self.pipeline = KPipeline(lang_code="b")

    def speak(self, text: str) -> None:
        import numpy as np

        for _, _, audio in self.pipeline(text, voice=self.voice):
            self.sd.play(np.asarray(audio), samplerate=24000)
            self.sd.wait()

