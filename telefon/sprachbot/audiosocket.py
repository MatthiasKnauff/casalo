"""Asterisk AudioSocket – einfaches TCP-Protokoll.

Jedes Paket: 1 Byte Typ, 2 Byte Länge (Big Endian), Nutzdaten.
  0x00 Auflegen · 0x01 Kennung (16 Byte UUID) · 0x03 DTMF · 0x10 Audio (8 kHz, 16 Bit, mono, little endian) · 0xFF Fehler
Asterisk schickt 20-ms-Pakete (320 Byte); zurück schicken wir dasselbe Format.
"""
import asyncio
import struct
import uuid

AUFLEGEN, KENNUNG, DTMF, AUDIO, FEHLER = 0x00, 0x01, 0x03, 0x10, 0xFF
RATE = 8000
FRAME = 320                     # 20 ms bei 8 kHz, 16 Bit


def paket(typ, daten=b""):
    return struct.pack(">BH", typ, len(daten)) + daten


async def lesen(reader):
    """→ (typ, daten) oder (None, b'') bei Verbindungsende."""
    try:
        kopf = await reader.readexactly(3)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None, b""
    typ, laenge = struct.unpack(">BH", kopf)
    daten = await reader.readexactly(laenge) if laenge else b""
    return typ, daten


def kennung(daten):
    return str(uuid.UUID(bytes=daten))


class Ausgabe:
    """Spielt Audio im 20-ms-Takt ab; abbrechbar (Anrufer redet dazwischen)."""

    def __init__(self, writer):
        self.writer = writer
        self._task = None

    @property
    def spricht(self):
        return self._task is not None and not self._task.done()

    async def _abspielen(self, pcm):
        loop = asyncio.get_running_loop()
        start = loop.time()
        for n, i in enumerate(range(0, len(pcm), FRAME)):
            stueck = pcm[i:i + FRAME]
            if len(stueck) < FRAME:
                stueck += b"\x00" * (FRAME - len(stueck))
            self.writer.write(paket(AUDIO, stueck))
            await self.writer.drain()
            # gleichmäßiger Takt ohne Drift
            warte = start + (n + 1) * 0.02 - loop.time()
            if warte > 0:
                await asyncio.sleep(warte)

    def abspielen(self, pcm):
        self.abbrechen()
        self._task = asyncio.ensure_future(self._abspielen(pcm))
        return self._task

    async def fertig(self):
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def abbrechen(self):
        if self.spricht:
            self._task.cancel()

    def auflegen(self):
        try:
            self.writer.write(paket(AUFLEGEN))
        except Exception:
            pass
