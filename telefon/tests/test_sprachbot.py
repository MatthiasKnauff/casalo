import asyncio
import os
import struct
import sys
import uuid

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sprachbot import audiosocket as aso                    # noqa: E402
from sprachbot.gespraech import Gespraech                    # noqa: E402
from sprachbot.haupt import Bot                              # noqa: E402
from sprachbot.pseudonym import Pseudonymisierer             # noqa: E402
from sprachbot.stimme import umrechnen                       # noqa: E402


def lauf(coro):
    return asyncio.run(coro)


def test_audiosocket_pakete():
    async def t():
        r = asyncio.StreamReader()
        u = uuid.uuid4()
        r.feed_data(aso.paket(aso.KENNUNG, u.bytes) + aso.paket(aso.AUDIO, b"\x01\x00" * 160) + aso.paket(aso.AUFLEGEN))
        r.feed_eof()
        assert await aso.lesen(r) == (aso.KENNUNG, u.bytes)
        typ, daten = await aso.lesen(r)
        assert typ == aso.AUDIO and len(daten) == 320
        assert (await aso.lesen(r))[0] == aso.AUFLEGEN
        assert (await aso.lesen(r))[0] is None
        assert aso.kennung(u.bytes) == str(u)
    lauf(t())
    assert aso.paket(aso.AUDIO, b"ab") == struct.pack(">BH", 0x10, 2) + b"ab"


def test_umrechnen_22050_auf_8000():
    t = np.arange(22050) / 22050
    ton = (np.sin(2 * np.pi * 440 * t) * 10000).astype("<i2").tobytes()
    neu = umrechnen(ton, 22050, 8000)
    assert abs(len(neu) // 2 - 8000) <= 1
    assert np.abs(np.frombuffer(neu, "<i2")).max() > 5000


def test_pseudonym_hin_und_zurueck():
    p = Pseudonymisierer()
    p.kontext_merken({"anrufer": {"vorname": "Sabine", "nachname": "Becker"},
                      "gemeinschaften": [{"adresse": "Musterweg 1, 45130 Essen"}]})
    t = p.hin("Hallo, hier Sabine Becker aus dem Musterweg 1. Frau Becker hat Wasser.")
    assert "Becker" not in t and "Musterweg" not in t and "[Person" in t
    assert p.rueck(t) == "Hallo, hier Sabine Becker aus dem Musterweg 1. Frau Becker hat Wasser."
    # Wortgrenzen: kurze Namen nicht in anderen Wörtern ersetzen
    p.merken("Can", "Vorname")
    assert p.hin("Cannstatt") == "Cannstatt"


def test_pseudonym_aus():
    p = Pseudonymisierer(aktiv=False)
    p.kontext_merken({"anrufer": {"vorname": "A", "nachname": "Becker"}})
    assert p.hin("Becker") == "Becker"


class FakeCasalo:
    def __init__(self):
        self.aufrufe = []
        self.ident = "nummer"

    async def kontext(self, aid):
        k = {"identifiziert": self.ident, "anrufer": {"anrede": "Frau", "vorname": "Sabine", "nachname": "Becker"},
             "gemeinschaften": [{"adresse": "Musterweg 1, 45130 Essen"}]}
        if self.ident == "eindeutig":
            k["persoenlich"] = {"hausgeld_monat": "318,00 €"}
        return k

    async def pruefen(self, aid, einheit, plz):
        self.aufrufe.append(("pruefen", einheit, plz))
        if einheit == "Wohnung 1" and plz == "45130":
            self.ident = "eindeutig"
            return {"ergebnis": "eindeutig"}
        return {"ergebnis": "falsch"}

    async def vorgang(self, aid, titel, text, notfallart=""):
        self.aufrufe.append(("vorgang", titel, text, notfallart))
        return {"vorgang_id": 7, "notfall": {"aktion": "weiterleiten", "nummer": "01711234567"}} if notfallart else {"vorgang_id": 7}

    async def ende(self, aid, **d):
        self.aufrufe.append(("ende", d))

    async def ki_protokoll(self, aid, inhalt):
        self.aufrufe.append(("ki", inhalt))


class FakeClaude:
    """Spielt eine feste Abfolge von Antworten ab und merkt sich, was gesendet wurde."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.gesendet = []

    async def antwort(self, system, nachrichten, werkzeuge):
        self.gesendet.append((system, [dict(n) for n in nachrichten]))
        return {"content": self.antworten.pop(0)}


def test_gespraech_identifikation_und_notfall():
    casalo = FakeCasalo()
    claude = FakeClaude([
        [{"type": "tool_use", "id": "t1", "name": "identitaet_pruefen", "input": {"einheit": "Wohnung 1", "plz": "45130"}}],
        [{"type": "text", "text": "Danke, [Person 2]. Ihr Hausgeld beträgt dreihundertachtzehn Euro im Monat."}],
        [{"type": "tool_use", "id": "t2", "name": "vorgang_anlegen",
          "input": {"titel": "Wasser im Keller", "beschreibung": "Bei [Person 2] läuft Wasser", "notfallart": "wasser"}}],
        [{"type": "text", "text": "Ich verbinde Sie mit dem Notdienst."},
         {"type": "tool_use", "id": "t3", "name": "weiterleiten", "input": {"grund": "Wasser"}}],
    ])

    async def t():
        g = Gespraech(casalo, claude, Pseudonymisierer(), {"anruf_id": 1, "begruessung": "Guten Tag. Wie kann ich helfen?"})
        gruss = await g.begruessung()
        assert "Frau Becker" in gruss
        a = await g.eingabe("Wie hoch ist mein Hausgeld? Wohnung 1, 45130.")
        assert a == [("sagen", "Danke, Becker. Ihr Hausgeld beträgt dreihundertachtzehn Euro im Monat.")]
        # an Claude ging nichts im Klartext
        system, nachrichten = claude.gesendet[-1]
        assert "Becker" not in system and "Musterweg" not in system
        assert "persoenlich" in system                     # nach Prüfung im Kontext
        a = await g.eingabe("Und bei mir läuft Wasser aus der Decke!")
        assert a[-1] == ("weiterleiten", "01711234567")
        v = [x for x in casalo.aufrufe if x[0] == "vorgang"][0]
        assert v[2] == "Bei Becker läuft Wasser" and v[3] == "wasser"   # Klartext zurück an Casalo
        await g.abschliessen()
        ende = [x for x in casalo.aufrufe if x[0] == "ende"][0][1]
        assert ende["status"] == "weitergeleitet" and ende["weiterleitung"] == "01711234567"
    lauf(t())


def test_weiterleiten_ohne_notfall_verweigert():
    casalo = FakeCasalo()
    claude = FakeClaude([
        [{"type": "tool_use", "id": "t1", "name": "weiterleiten", "input": {"grund": "will Mensch"}}],
        [{"type": "text", "text": "Das geht leider nicht, ich lege einen Rückruf an."}],
    ])

    async def t():
        g = Gespraech(casalo, claude, Pseudonymisierer(), {"anruf_id": 1})
        await g.begruessung()
        a = await g.eingabe("Verbinden Sie mich mit einem Menschen.")
        assert ("weiterleiten", "") not in a and all(x[0] == "sagen" for x in a)
    lauf(t())


def test_ki_stoerung_fuehrt_zu_rueckruf():
    casalo = FakeCasalo()

    class Kaputt:
        async def antwort(self, *a):
            raise RuntimeError("nicht erreichbar")

    async def t():
        g = Gespraech(casalo, Kaputt(), Pseudonymisierer(), {"anruf_id": 1})
        await g.begruessung()
        a = await g.eingabe("Hallo?")
        assert a[-1] == ("auflegen", "") and "112" in a[0][1]
        assert g.nachrichten == []
        assert any(x[0] == "vorgang" for x in casalo.aufrufe)
    lauf(t())


def test_steuerung_start_und_weiter():
    bot = Bot(None, None, None, None)

    async def t():
        assert await bot.http_route("POST", "/start/abc", "nummer=%2B49201123456") == "ok"
        assert bot.sitzungen["abc"]["nummer"] == "+49201123456"
        bot.sitzungen["abc"]["weiter"] = "0171"
        assert await bot.http_route("GET", "/weiter/abc", "") == "0171"
        assert await bot.http_route("GET", "/weiter/abc", "") == ""
    lauf(t())
