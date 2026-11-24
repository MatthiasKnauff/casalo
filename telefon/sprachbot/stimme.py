"""Sprachausgabe mit Piper (lokal, ohne Grafikkarte) und Umrechnung auf 8 kHz fürs Telefon."""
import asyncio
import os

import numpy as np


def umrechnen(pcm, von_rate, nach_rate=8000):
    """16-Bit-PCM von einer Abtastrate auf eine andere bringen (lineare Interpolation, vorher einfacher Tiefpass)."""
    if von_rate == nach_rate or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if nach_rate < von_rate:
        # gleitender Mittelwert gegen Aliasing
        k = max(1, int(round(von_rate / nach_rate)))
        if k > 1:
            x = np.convolve(x, np.ones(k) / k, mode="same")
    dauer = len(x) / von_rate
    n_neu = int(round(dauer * nach_rate))
    alt_t = np.linspace(0, dauer, num=len(x), endpoint=False)
    neu_t = np.linspace(0, dauer, num=n_neu, endpoint=False)
    y = np.interp(neu_t, alt_t, x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


class Piper:
    def __init__(self, modell=None):
        from piper import PiperVoice
        self.voice = PiperVoice.load(modell or os.environ.get("PIPER_MODELL", "/opt/piper/stimme.onnx"))
        self.rate = self.voice.config.sample_rate

    def _sprechen(self, text):
        teile = []
        if hasattr(self.voice, "synthesize_stream_raw"):          # piper-tts 1.2
            for stueck in self.voice.synthesize_stream_raw(text):
                teile.append(stueck)
        else:                                                    # piper-tts ≥ 1.3
            for chunk in self.voice.synthesize(text):
                teile.append(chunk.audio_int16_bytes)
        return umrechnen(b"".join(teile), self.rate, 8000)

    async def sprechen(self, text):
        return await asyncio.get_running_loop().run_in_executor(None, self._sprechen, text)
