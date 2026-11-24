"""Gesprächsführung: Text rein, Aktionen raus (sagen / weiterleiten / auflegen).

Unabhängig vom Audio – dadurch ohne Telefon testbar.
"""
import json
import time

NOTFALLARTEN = ["wasser", "heizung", "sturm", "strom", "aufzug", "sonstiges"]

WERKZEUGE = [
    {"name": "identitaet_pruefen",
     "description": "Prüft die Rückfrage: Einheit (Wohnungsnummer) und Postleitzahl der Wohnanlage. Erst danach sind "
                    "persönliche Auskünfte erlaubt.",
     "input_schema": {"type": "object", "properties": {
         "einheit": {"type": "string", "description": "z. B. 'Wohnung 3' oder 'W 03'"},
         "plz": {"type": "string", "description": "Postleitzahl der Wohnanlage, fünfstellig"}},
         "required": ["einheit", "plz"]}},
    {"name": "vorgang_anlegen",
     "description": "Legt das Anliegen als Vorgang für die Verwaltung an (Schaden, Frage, Rückrufwunsch). "
                    "Bei einem Notfall notfallart setzen – die Antwort sagt dann, was zu tun ist.",
     "input_schema": {"type": "object", "properties": {
         "titel": {"type": "string", "description": "kurz, z. B. 'Wasserfleck Decke Bad'"},
         "beschreibung": {"type": "string"},
         "notfallart": {"type": "string", "enum": [""] + NOTFALLARTEN}},
         "required": ["titel", "beschreibung"]}},
    {"name": "weiterleiten",
     "description": "Verbindet den Anrufer mit der Notdienstnummer – nur wenn vorgang_anlegen das als Aktion vorgibt.",
     "input_schema": {"type": "object", "properties": {"grund": {"type": "string"}}, "required": ["grund"]}},
    {"name": "auflegen",
     "description": "Beendet das Gespräch, nachdem du dich verabschiedet hast.",
     "input_schema": {"type": "object", "properties": {}}},
]

REGELN = """Du bist der Telefonassistent einer Hausverwaltung für Wohnungseigentümergemeinschaften (WEG).
Du sprichst am Telefon: antworte kurz (höchstens zwei Sätze), freundlich, in gesprochenem Deutsch, ohne Aufzählungszeichen,
ohne Abkürzungen, Beträge ausgeschrieben wie „dreihundertachtzehn Euro“.

Regeln:
- Nutze nur Angaben aus KONTEXT. Erfinde nichts. Wenn du etwas nicht weißt, biete einen Rückruf an und lege einen Vorgang an.
- Stufe der Identifikation steht in KONTEXT.identifiziert:
  „nein“: Rufnummer unbekannt – nur Allgemeines (Bürozeiten, Termine der Versammlungen). Persönliches nur per Rückruf.
  „nummer“: Rufnummer gehört zu einem Eigentümer – du darfst ihn mit Namen ansprechen und den Stand seiner Vorgänge nennen.
  „eindeutig“: zusätzlich Hausgeld, Rückstand, Ergebnis der Abrechnung (KONTEXT.persoenlich).
- Fragt jemand nach Persönlichem und ist nicht „eindeutig“: frage nach Wohnungsnummer und Postleitzahl der Wohnanlage
  und rufe identitaet_pruefen auf. Nenne niemals selbst die richtige Antwort und gib keine Hinweise.
- Bei Schäden oder Anliegen: kurz nachfragen (was, wo, seit wann), dann vorgang_anlegen. Bei Gefahr (Wasser läuft,
  Heizung ganz ausgefallen im Winter, Sturmschaden, Aufzug steckt mit Person fest, Stromausfall) notfallart setzen und
  der Antwort folgen: „weiterleiten“ → sag, dass du verbindest, und rufe weiterleiten auf; „ansagen“ → nenne Nummer und
  Ansage langsam; „aufnehmen“ → beruhige und nenne den Rückruf.
- Bei akuter Gefahr für Menschen (Feuer, Gasgeruch, Verletzte): sofort auf Notruf 112 hinweisen.
- Namen und Adressen stehen als Kennungen wie [Person 1] oder [Adresse 1] da – verwende sie genau so.
- Am Ende kurz zusammenfassen, verabschieden und auflegen aufrufen."""


class Gespraech:
    def __init__(self, casalo, claude, pseudo, start, nummer=""):
        self.casalo = casalo
        self.claude = claude
        self.pseudo = pseudo
        self.aid = start["anruf_id"]
        self.start = start
        self.nummer = nummer
        self.kontext = {}
        self.nachrichten = []
        self.verlauf = []                     # Klartext für Casalo
        self.beginn = time.monotonic()
        self.status = None
        self.anliegen = ""
        self.weiter_nummer = ""
        self.erlaubte_weiterleitung = ""
        self.beendet = False

    async def kontext_laden(self):
        self.kontext = await self.casalo.kontext(self.aid)
        self.pseudo.kontext_merken(self.kontext)

    def system(self):
        k = self.pseudo.objekt_hin(self.kontext)
        gruss = self.pseudo.hin(getattr(self, "begruessung_text", ""))
        return (REGELN + f"\n\nDu hast den Anrufer bereits begrüßt mit: „{gruss}“\n\nKONTEXT:\n"
                + json.dumps(k, ensure_ascii=False, indent=1))

    async def begruessung(self):
        await self.kontext_laden()
        text = self.start.get("begruessung") or "Hausverwaltung, guten Tag. Wie kann ich helfen?"
        a = self.kontext.get("anrufer")
        if a and a.get("nachname"):
            text = text.replace("Wie kann ich helfen?",
                                f"Spreche ich mit {(a.get('anrede') + ' ') if a.get('anrede') else ''}{a['nachname']}? Wie kann ich helfen?")
        self.verlauf.append({"wer": "ki", "text": text})
        self.begruessung_text = text
        return text

    async def _werkzeug(self, name, eingabe):
        if name == "identitaet_pruefen":
            erg = await self.casalo.pruefen(self.aid, eingabe.get("einheit", ""), eingabe.get("plz", ""))
            if erg.get("ergebnis") == "eindeutig":
                await self.kontext_laden()
                erg["hinweis"] = "Identität bestätigt. KONTEXT.persoenlich ist jetzt verfügbar."
            return erg
        if name == "vorgang_anlegen":
            titel = self.pseudo.rueck(eingabe.get("titel", ""))
            text = self.pseudo.rueck(eingabe.get("beschreibung", ""))
            self.anliegen = titel
            erg = await self.casalo.vorgang(self.aid, titel, text, eingabe.get("notfallart", ""))
            self.status = "vorgang"
            n = erg.get("notfall") or {}
            if n.get("aktion") == "weiterleiten" and n.get("nummer"):
                self.erlaubte_weiterleitung = n["nummer"]
            return erg
        if name == "weiterleiten":
            if not self.erlaubte_weiterleitung:
                return {"fehler": "Weiterleiten ist nur bei einem Notfall mit hinterlegter Nummer möglich."}
            self.weiter_nummer = self.erlaubte_weiterleitung
            self.status = "weitergeleitet"
            return {"ok": True}
        if name == "auflegen":
            self.beendet = True
            return {"ok": True}
        return {"fehler": "unbekanntes Werkzeug"}

    async def eingabe(self, text):
        """Anrufer hat etwas gesagt → Liste von Aktionen [("sagen", text) | ("weiterleiten", nummer) | ("auflegen", "")]."""
        self.verlauf.append({"wer": "anrufer", "text": text})
        stand = len(self.nachrichten)
        self.nachrichten.append({"role": "user", "content": self.pseudo.hin(text)})
        aktionen = []
        for _ in range(5):
            system = self.system()
            try:
                antwort = await self.claude.antwort(system, self.nachrichten, WERKZEUGE)
            except Exception:
                # KI nicht erreichbar: Verlauf zurücksetzen, Rückruf anlegen, freundlich beenden
                del self.nachrichten[stand:]
                if self.status != "vorgang":
                    try:
                        await self.casalo.vorgang(self.aid, "Rückruf erbeten (Assistent gestört)", text, "")
                        self.status = "rueckruf"
                    except Exception:
                        pass
                satz = ("Entschuldigung, ich habe gerade eine technische Störung. Die Verwaltung ruft Sie zurück. "
                        "Bei einem Notfall mit Gefahr wählen Sie bitte die 112.")
                self.verlauf.append({"wer": "ki", "text": satz})
                self.beendet = True
                return [("sagen", satz), ("auflegen", "")]
            await self.casalo.ki_protokoll(self.aid, json.dumps(
                {"system_kurz": system[-1500:], "letzte_nachricht": self.nachrichten[-1]}, ensure_ascii=False)[:6000])
            inhalt = antwort.get("content", [])
            self.nachrichten.append({"role": "assistant", "content": inhalt})
            for block in inhalt:
                if block.get("type") == "text" and block.get("text", "").strip():
                    klar = self.pseudo.rueck(block["text"].strip())
                    self.verlauf.append({"wer": "ki", "text": klar})
                    aktionen.append(("sagen", klar))
            werkzeuge = [b for b in inhalt if b.get("type") == "tool_use"]
            if not werkzeuge:
                break
            ergebnisse = []
            for w in werkzeuge:
                erg = await self._werkzeug(w["name"], w.get("input") or {})
                ergebnisse.append({"type": "tool_result", "tool_use_id": w["id"],
                                   "content": json.dumps(self.pseudo.objekt_hin(erg), ensure_ascii=False)})
            self.nachrichten.append({"role": "user", "content": ergebnisse})
            if self.weiter_nummer or self.beendet:
                break
        if self.weiter_nummer:
            aktionen.append(("weiterleiten", self.weiter_nummer))
        elif self.beendet:
            aktionen.append(("auflegen", ""))
        return aktionen

    async def abschliessen(self):
        status = self.status or ("beantwortet" if len(self.verlauf) > 2 else "rueckruf")
        try:
            await self.casalo.ende(self.aid, dauer_s=int(time.monotonic() - self.beginn), verlauf=self.verlauf,
                                   anliegen=self.anliegen, status=status, weiterleitung=self.weiter_nummer)
        except Exception:
            pass
