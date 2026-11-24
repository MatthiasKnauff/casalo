"""Spracherkennung live über Deepgram (WebSocket, 8 kHz linear16)."""
import asyncio
import json
import os


class Deepgram:
    """Schickt Audio, liefert Ereignisse: ("zwischen", text) während der Anrufer spricht, ("fertig", satz) am Ende."""

    def __init__(self, schluessel=None, modell=None):
        self.schluessel = schluessel or os.environ["DEEPGRAM_API_KEY"]
        self.modell = modell or os.environ.get("DEEPGRAM_MODELL", "nova-2")
        self.ereignisse = asyncio.Queue()
        self._ws = None
        self._puffer = []

    async def verbinden(self):
        import websockets
        url = ("wss://api.deepgram.com/v1/listen?encoding=linear16&sample_rate=8000&channels=1&language=de"
               f"&model={self.modell}&punctuate=true&smart_format=true&interim_results=true"
               "&endpointing=400&utterance_end_ms=1200&vad_events=true")
        kopf = {"Authorization": f"Token {self.schluessel}"}
        try:
            self._ws = await websockets.connect(url, additional_headers=kopf, max_size=None)
        except TypeError:                                  # ältere websockets-Versionen
            self._ws = await websockets.connect(url, extra_headers=kopf, max_size=None)
        asyncio.ensure_future(self._empfangen())
        asyncio.ensure_future(self._wachhalten())

    async def senden(self, pcm):
        if self._ws:
            try:
                await self._ws.send(pcm)
            except Exception:
                pass

    async def _wachhalten(self):
        while self._ws:
            await asyncio.sleep(8)
            try:
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                return

    def _satz_fertig(self):
        satz = " ".join(self._puffer).strip()
        self._puffer = []
        if satz:
            self.ereignisse.put_nowait(("fertig", satz))

    async def _empfangen(self):
        try:
            async for roh in self._ws:
                m = json.loads(roh)
                typ = m.get("type")
                if typ == "Results":
                    alt = (m.get("channel", {}).get("alternatives") or [{}])[0]
                    text = (alt.get("transcript") or "").strip()
                    if not text:
                        if m.get("speech_final"):
                            self._satz_fertig()
                        continue
                    if m.get("is_final"):
                        self._puffer.append(text)
                        if m.get("speech_final"):
                            self._satz_fertig()
                    else:
                        self.ereignisse.put_nowait(("zwischen", text))
                elif typ == "UtteranceEnd":
                    self._satz_fertig()
        except Exception:
            pass
        finally:
            self.ereignisse.put_nowait(("ende", ""))

    async def schliessen(self):
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
