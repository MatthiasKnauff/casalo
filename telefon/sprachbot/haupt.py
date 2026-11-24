"""Sprachbot: verbindet Asterisk (AudioSocket) mit Deepgram, Claude, Piper und Casalo.

Ablauf je Anruf (siehe asterisk/extensions.conf):
  1. Asterisk meldet den Anruf per HTTP:  POST 127.0.0.1:9093/start/<id>   (Rufnummer)
  2. Asterisk öffnet AudioSocket zu 127.0.0.1:9092 mit derselben <id>
  3. Endet das Gespräch, fragt Asterisk GET /weiter/<id> – kommt eine Nummer zurück, wird dorthin verbunden.
"""
import asyncio
import logging
import os
import urllib.parse

from . import audiosocket as aso
from .gespraech import Gespraech
from .pseudonym import Pseudonymisierer

log = logging.getLogger("sprachbot")
STILLE_NACHFRAGE = 15
STILLE_ENDE = 30


class Bot:
    def __init__(self, casalo, claude, stimme, hoeren_fabrik):
        self.casalo = casalo
        self.claude = claude
        self.stimme = stimme
        self.hoeren_fabrik = hoeren_fabrik
        self.sitzungen = {}          # id -> {"nummer", "gespraech", "weiter"}

    # ------------------------------------------------------------ HTTP für den Wählplan
    async def http(self, reader, writer):
        try:
            kopf = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            zeilen = kopf.decode("latin-1").split("\r\n")
            methode, pfad, _ = zeilen[0].split(" ", 2)
            laenge = next((int(z.split(":", 1)[1]) for z in zeilen if z.lower().startswith("content-length:")), 0)
            koerper = (await reader.readexactly(laenge)).decode() if laenge else ""
            antwort = await self.http_route(methode, pfad, koerper)
        except Exception as e:
            log.warning("HTTP: %s", e)
            antwort = ""
        daten = antwort.encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: "
                     + str(len(daten)).encode() + b"\r\n\r\n" + daten)
        await writer.drain()
        writer.close()

    async def http_route(self, methode, pfad, koerper):
        teile = pfad.strip("/").split("/")
        if len(teile) == 2 and teile[0] == "start":
            werte = urllib.parse.parse_qs(koerper or urllib.parse.urlparse(pfad).query)
            self.sitzungen[teile[1]] = {"nummer": (werte.get("nummer") or [""])[0], "weiter": ""}
            return "ok"
        if len(teile) == 2 and teile[0] == "weiter":
            s = self.sitzungen.pop(teile[1], None)
            return (s or {}).get("weiter", "")
        return ""

    # ------------------------------------------------------------ ein Anruf
    async def anruf(self, reader, writer):
        typ, daten = await aso.lesen(reader)
        if typ != aso.KENNUNG:
            writer.close()
            return
        cid = aso.kennung(daten)
        sitzung = self.sitzungen.setdefault(cid, {"nummer": "", "weiter": ""})
        ausgabe = aso.Ausgabe(writer)
        hoeren = self.hoeren_fabrik()
        g = None
        try:
            start = await self.casalo.start(sitzung["nummer"])
            g = Gespraech(self.casalo, self.claude, Pseudonymisierer(start.get("anonymisieren", True)), start,
                          sitzung["nummer"])
            await hoeren.verbinden()
            gruss = await g.begruessung()
            ausgabe.abspielen(await self.stimme.sprechen(gruss))
            empfang = asyncio.ensure_future(self._audio_weiterleiten(reader, hoeren))
            await self._dialog(g, hoeren, ausgabe, sitzung, empfang)
        except Exception as e:
            log.exception("Anruf %s: %s", cid, e)
            try:
                ausgabe.abspielen(await self.stimme.sprechen(
                    "Entschuldigung, es gibt eine technische Störung. Bitte rufen Sie später noch einmal an."))
                await ausgabe.fertig()
            except Exception:
                pass
        finally:
            if g:
                await g.abschliessen()
            await hoeren.schliessen()
            ausgabe.auflegen()
            try:
                writer.close()
            except Exception:
                pass

    async def _audio_weiterleiten(self, reader, hoeren):
        while True:
            typ, daten = await aso.lesen(reader)
            if typ is None or typ in (aso.AUFLEGEN, aso.FEHLER):
                await hoeren.ereignisse.put(("aufgelegt", ""))
                return
            if typ == aso.AUDIO:
                await hoeren.senden(daten)

    async def _dialog(self, g, hoeren, ausgabe, sitzung, empfang):
        nachgefragt = False
        while True:
            try:
                art, text = await asyncio.wait_for(hoeren.ereignisse.get(),
                                                   STILLE_ENDE if nachgefragt else STILLE_NACHFRAGE)
            except asyncio.TimeoutError:
                if ausgabe.spricht:
                    continue
                if nachgefragt:
                    ausgabe.abspielen(await self.stimme.sprechen("Ich lege jetzt auf. Auf Wiederhören."))
                    await ausgabe.fertig()
                    return
                nachgefragt = True
                ausgabe.abspielen(await self.stimme.sprechen("Sind Sie noch da?"))
                continue
            if art in ("aufgelegt", "ende"):
                empfang.cancel()
                return
            if art == "zwischen":
                if ausgabe.spricht and len(text.split()) >= 2:
                    ausgabe.abbrechen()          # Anrufer redet dazwischen
                continue
            if art != "fertig":
                continue
            nachgefragt = False
            for aktion, wert in await g.eingabe(text):
                if aktion == "sagen":
                    await ausgabe.fertig()
                    ausgabe.abspielen(await self.stimme.sprechen(wert))
                elif aktion == "weiterleiten":
                    await ausgabe.fertig()
                    sitzung["weiter"] = wert
                    return
                elif aktion == "auflegen":
                    await ausgabe.fertig()
                    return


async def main():
    from .dienste import Casalo, Claude
    from .hoeren import Deepgram
    from .stimme import Piper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bot = Bot(Casalo(), Claude(), Piper(), Deepgram)
    s1 = await asyncio.start_server(bot.anruf, "127.0.0.1", int(os.environ.get("AUDIOSOCKET_PORT", 9092)))
    s2 = await asyncio.start_server(bot.http, "127.0.0.1", int(os.environ.get("STEUER_PORT", 9093)))
    log.info("Sprachbot bereit (AudioSocket %s, Steuerung %s)", s1.sockets[0].getsockname(), s2.sockets[0].getsockname())
    async with s1, s2:
        await asyncio.gather(s1.serve_forever(), s2.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
